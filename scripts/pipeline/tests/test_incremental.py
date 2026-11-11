"""Verify selective invalidation and receipt-backed publication without wheel downloads."""

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import tomli_w

from scripts.build.android_python import AndroidABI, PythonVersion
from scripts.build.native_model import NativeLibrary
from scripts.pipeline.__main__ import main
from scripts.pipeline.artifacts import collect, record
from scripts.pipeline.fingerprint import Fingerprinter, fingerprint
from scripts.pipeline.incremental import RebuildMode, incremental_plan
from scripts.pipeline.model import AlreadyPublished, Plan, load_plan
from scripts.pipeline.planning import write_actions_plan
from scripts.pipeline.publish import Published, publish
from scripts.pipeline.published import (
    read_store,
    wheel_blob,
    write_receipts,
)
from scripts.pipeline.tests.test_pipeline import TARGET, make_wheel
from scripts.recipes.model import (
    Backend,
    ConfigSetting,
    EnvironmentVariable,
    Publication,
)

ROOT = Path(__file__).resolve().parents[3]


def git(root, *args):
    return subprocess.check_output(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
        cwd=root,
        stderr=subprocess.PIPE,
        text=True,
    ).strip()


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.code = self.root / "code"
        self.code.mkdir()
        for name in ("scripts", ".github"):
            shutil.copytree(
                ROOT / name,
                self.code / name,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        shutil.copy2(ROOT / "pyproject.toml", self.code / "pyproject.toml")
        self.store = self.root / "store"
        self.store.mkdir()
        git(self.store, "init")
        (self.store / "README").write_text("wheel store")
        git(self.store, "add", ".")
        git(self.store, "commit", "-m", "initial")
        self.plan = Plan(Publication.PUBLISH, (TARGET,))

    def publish_fixture(self):
        plan = incremental_plan(
            self.plan, self.code, read_store(self.store), RebuildMode.CHANGED
        )
        artifacts = self.root / "artifacts"
        folder = artifacts / TARGET.artifact
        make_wheel(folder)
        record(folder, TARGET, plan.fingerprints[0])
        wheels = collect(artifacts, plan)
        receipts = self.root / "receipts"
        receipts.mkdir()
        files = {**wheels, **write_receipts(plan, artifacts, wheels, receipts)}
        for name, source in files.items():
            destination = self.store / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        git(self.store, "add", ".")
        git(self.store, "commit", "-m", "publish")
        return plan, artifacts, next(iter(wheels))

    def test_successful_publication_skips_exact_targets_and_never_reads_wheel_bytes(
        self,
    ):
        original, _, name = self.publish_fixture()
        # 稀疏检出可以没有任何 wheel 文件，Git 树仍能核对存在性和内容身份。
        (self.store / name).unlink()
        store = read_store(self.store)
        plan = incremental_plan(self.plan, self.code, store, RebuildMode.CHANGED)
        self.assertEqual(plan.targets, ())
        self.assertEqual(plan.fingerprints, ())
        self.assertIsInstance(plan.skipped[0], AlreadyPublished)
        self.assertEqual(plan.skipped[0].fingerprint, original.fingerprints[0].sha256)
        output = self.root / "outputs"
        path = self.root / "plan.json"
        write_actions_plan(plan, path, output)
        self.assertEqual(load_plan(path), plan)
        self.assertIn("has_jobs=false", output.read_text())
        self.assertIn("has_reused=true", output.read_text())

    def test_force_rebuild_and_new_python_target_keep_existing_parallel_targets(self):
        self.publish_fixture()
        store = read_store(self.store)
        forced = incremental_plan(self.plan, self.code, store, RebuildMode.FORCE)
        self.assertEqual(forced.targets, self.plan.targets)
        added = replace(TARGET, python=PythonVersion.PY314)
        expanded = replace(self.plan, targets=(TARGET, added))
        incremental = incremental_plan(expanded, self.code, store, RebuildMode.CHANGED)
        self.assertEqual(incremental.targets, (added,))
        path = self.root / "pending.json"
        incremental.write(path)
        self.assertEqual(load_plan(path), incremental)

    def test_actions_entry_keeps_complete_artifacts_for_build_only_mode(self):
        self.publish_fixture()
        event = self.root / "event.json"
        event.write_text(json.dumps({"inputs": {"rebuild": "changed"}}))
        output = self.root / "plan.json"
        summary = self.root / "summary"
        args = [
            "pipeline",
            "plan",
            "--event",
            str(event),
            "--event-name",
            "workflow_dispatch",
            "--published-store",
            str(self.store),
            "--github-output",
            str(self.root / "outputs"),
            "--output",
            str(output),
        ]
        for publication, expected in (
            (Publication.BUILD, (TARGET,)),
            (Publication.PUBLISH, ()),
        ):
            with (
                self.subTest(publication=publication),
                patch("sys.argv", args),
                patch(
                    "scripts.pipeline.__main__.recipe_plan",
                    return_value=replace(self.plan, publication=publication),
                ),
                patch("scripts.pipeline.__main__.Path.cwd", return_value=self.code),
                patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(summary)}),
                redirect_stdout(io.StringIO()),
            ):
                main()
                self.assertEqual(load_plan(output).targets, expected)
        self.assertIn("模式：`force`", summary.read_text())
        self.assertIn("模式：`changed`", summary.read_text())

    def test_wheel_and_receipt_are_published_atomically(self):
        remote = self.root / "remote.git"
        git(self.root, "init", "--bare", str(remote))
        git(self.store, "remote", "add", "origin", str(remote))
        git(self.store, "push", "origin", "HEAD:refs/heads/wheels")
        plan = incremental_plan(
            self.plan, self.code, read_store(self.store), RebuildMode.CHANGED
        )
        artifacts = self.root / "artifacts"
        folder = artifacts / TARGET.artifact
        make_wheel(folder)
        record(folder, TARGET, plan.fingerprints[0])
        wheels = collect(artifacts, plan)
        destination = self.root / "receipts"
        destination.mkdir()
        files = {**wheels, **write_receipts(plan, artifacts, wheels, destination)}
        result = publish(self.store, files, "test-run")
        self.assertIsInstance(result, Published)
        self.assertEqual(git(remote, "rev-list", "--count", "wheels"), "2")
        git(self.store, "fetch", "origin", "wheels")
        git(self.store, "checkout", "--detach", "FETCH_HEAD")
        reused = incremental_plan(
            self.plan, self.code, read_store(self.store), RebuildMode.CHANGED
        )
        self.assertEqual(reused.targets, ())

    def test_historical_wheel_without_receipt_is_built_once(self):
        (self.store / "legacy.whl").write_bytes(b"historical wheel")
        git(self.store, "add", ".")
        git(self.store, "commit", "-m", "legacy")
        result = incremental_plan(
            self.plan, self.code, read_store(self.store), RebuildMode.CHANGED
        )
        self.assertEqual(result.targets, self.plan.targets)
        self.assertEqual(len(result.fingerprints), 1)

    def test_deleted_or_replaced_published_wheel_is_rebuilt(self):
        _, _, name = self.publish_fixture()
        (self.store / name).write_bytes(b"replacement")
        git(self.store, "add", ".")
        git(self.store, "commit", "-m", "replace")
        self.assertEqual(
            incremental_plan(
                self.plan, self.code, read_store(self.store), RebuildMode.CHANGED
            ).targets,
            self.plan.targets,
        )
        (self.store / name).unlink()
        git(self.store, "add", "-u")
        git(self.store, "commit", "-m", "remove")
        self.assertEqual(
            incremental_plan(
                self.plan, self.code, read_store(self.store), RebuildMode.CHANGED
            ).targets,
            self.plan.targets,
        )

    def test_corrupt_receipt_is_reported_instead_of_rebuilding_silently(self):
        self.publish_fixture()
        path = self.store / ".build-records" / f"{TARGET.artifact}.json"
        path.write_text("{broken")
        git(self.store, "add", ".")
        git(self.store, "commit", "-m", "corrupt")
        with self.assertRaises(json.JSONDecodeError):
            read_store(self.store)

    def test_manifest_fingerprint_mismatch_prevents_publication(self):
        plan, artifacts, _ = self.publish_fixture()
        path = artifacts / TARGET.artifact / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["fingerprint"] = "0" * 64
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "指纹"):
            collect(artifacts, plan)

    def test_source_environment_python_abi_and_api_each_invalidate(self):
        original = fingerprint(TARGET, self.code)
        changes = (
            replace(TARGET, source=replace(TARGET.source, sha="b" * 40)),
            replace(TARGET, python=PythonVersion.PY314),
            replace(TARGET, abi=AndroidABI.X86_64),
            replace(TARGET, api=26),
            replace(TARGET, environment=(EnvironmentVariable("EXAMPLE", "new"),)),
            replace(TARGET, config_settings=(ConfigSetting("features", "new"),)),
        )
        for target in changes:
            with self.subTest(target=target):
                self.assertNotEqual(fingerprint(target, self.code), original)
        self.assertEqual(
            fingerprint(
                replace(TARGET, source=replace(TARGET.source, ref="alias")), self.code
            ),
            original,
        )

    def test_backend_specific_code_does_not_invalidate_other_builders(self):
        c_source = replace(TARGET.source, backend=Backend.SETUPTOOLS)
        targets = (
            TARGET,
            replace(TARGET, source=c_source),
            replace(TARGET, source=c_source, python=PythonVersion.PY312),
        )
        before = [fingerprint(t, self.code) for t in targets]
        path = self.code / "scripts/build/chaquopy_adapter.py"
        path.write_text(path.read_text() + "\n# 修改旧版构建适配器。\n")
        after = [fingerprint(t, self.code) for t in targets]
        self.assertEqual(before[:2], after[:2])
        self.assertNotEqual(before[2], after[2])

    def test_native_configuration_is_scoped_to_selected_libraries(self):
        target = replace(TARGET, native_dependencies=(NativeLibrary.OPENBLAS,))
        before = [fingerprint(t, self.code) for t in (TARGET, target)]
        identify = Fingerprinter(self.code)
        import tomllib

        path = self.code / "scripts/build/native-libraries.toml"
        data = tomllib.loads(path.read_text())
        data["openblas"]["version"] = "test-version"
        path.write_text(tomli_w.dumps(data))
        after = [fingerprint(t, self.code) for t in (TARGET, target)]
        self.assertEqual(before[0], after[0])
        self.assertNotEqual(before[1], after[1])
        self.assertEqual(identify(target), before[1])

    def test_cross_file_changes_only_invalidate_the_target_which_uses_it(self):
        targets = tuple(
            replace(
                TARGET,
                source=replace(TARGET.source, backend=Backend.MESON),
                abi=abi,
                config_settings=(
                    ConfigSetting(
                        "setup-args",
                        f"--cross-file={{workspace}}/scripts/build/meson/{abi}.ini",
                    ),
                ),
            )
            for abi in AndroidABI
        )
        before = [fingerprint(t, self.code) for t in targets]
        path = self.code / "scripts/build/meson/arm64-v8a.ini"
        path.write_text(path.read_text() + "\n# 修改当前架构的配置。\n")
        after = [fingerprint(t, self.code) for t in targets]
        self.assertNotEqual(before[0], after[0])
        self.assertEqual(before[1], after[1])

    def test_plan_and_publication_changes_do_not_invalidate_built_wheels(self):
        before = fingerprint(TARGET, self.code)
        for name in (
            "scripts/pipeline/planning.py",
            "scripts/pipeline/publish.py",
            "scripts/pipeline/incremental.py",
        ):
            path = self.code / name
            path.write_text(path.read_text() + "\n# 仅调整调度或发布。\n")
        self.assertEqual(fingerprint(TARGET, self.code), before)

    def test_wheel_blob_matches_git_object_identity(self):
        path = self.root / "wheel.whl"
        path.write_bytes(b"example wheel bytes")
        self.assertEqual(wheel_blob(path), git(self.store, "hash-object", str(path)))


if __name__ == "__main__":
    unittest.main()
