"""Reuse successful targets from partial runs without changing their provenance."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build.android_python import AndroidABI, PythonVersion
from scripts.build.tests.test_wheel_abi import elf_with_dependency
from scripts.pipeline.artifacts import collect, record
from scripts.pipeline.model import (
    BuildFingerprint,
    GitSource,
    Plan,
    Target,
    UnsupportedPython,
    load_plan,
)
from scripts.pipeline.repackage import repackage, select_plan
from scripts.recipes.model import Backend, Publication
from scripts.testing.wheels import write_wheel


class RepackageTests(unittest.TestCase):
    def test_explicit_recipe_selection_keeps_all_selected_versions_and_skips(self):
        source = GitSource("owner/repo", "main", "a" * 40, ".", Backend.SETUPTOOLS)
        targets = tuple(
            Target(name, source, version, abi, 24)
            for name in ("retained", "excluded")
            for version in (PythonVersion.PY313, PythonVersion.PY314)
            for abi in AndroidABI
        )
        skipped = tuple(
            UnsupportedPython(name, PythonVersion.PY310, ">=3.11")
            for name in ("retained", "excluded")
        )
        fingerprints = tuple(BuildFingerprint(t.artifact, "b" * 64) for t in targets)
        plan = select_plan(
            Plan(Publication.BUILD, targets, skipped, fingerprints), ["retained"]
        )
        self.assertEqual(plan.targets, targets[:4])
        self.assertEqual(plan.skipped, skipped[:1])
        self.assertEqual(plan.fingerprints, fingerprints[:4])
        for names in ([], ["unknown"], ["retained", "retained"]):
            with (
                self.subTest(names=names),
                self.assertRaisesRegex(ValueError, "显式选择"),
            ):
                select_plan(plan, names)

    def test_partial_run_and_targeted_retry_form_one_complete_cohort(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            targets = tuple(
                Target(
                    "example",
                    GitSource("owner/repo", "main", "a" * 40, ".", Backend.SETUPTOOLS),
                    version,
                    AndroidABI.ARM64,
                    24,
                )
                for version in (PythonVersion.PY313, PythonVersion.PY314)
            )
            for run, target in zip(("first", "retry"), targets, strict=True):
                folder = root / run / target.artifact
                folder.mkdir(parents=True)
                tag = "cp" + target.python.replace(".", "")
                write_wheel(
                    folder / f"example-1.0-{tag}-{tag}-android_24_arm64_v8a.whl",
                    {
                        "example.so": elf_with_dependency(
                            f"libpython{target.python}.so"
                        ),
                        "example-1.0.dist-info/METADATA": b"Name: example\nVersion: 1.0\n",
                        "example-1.0.dist-info/WHEEL": f"Wheel-Version: 1.0\nTag: {tag}-{tag}-android_24_arm64_v8a\n".encode(),
                    },
                )
                record(folder, target)
            skipped = (UnsupportedPython("example", PythonVersion.PY310, ">=3.11"),)
            repackage(
                (
                    (
                        "1",
                        root / "first",
                        Plan(
                            Publication.BUILD,
                            targets,
                            skipped,
                            tuple(
                                BuildFingerprint(t.artifact, "b" * 64) for t in targets
                            ),
                        ),
                    ),
                    ("2", root / "retry", Plan(Publication.BUILD, targets[1:])),
                ),
                root / "output",
            )
            plan = load_plan(root / "output/build-plan.json")
            self.assertEqual(plan.targets, targets)
            self.assertEqual(plan.skipped, skipped)
            self.assertEqual(plan.fingerprints, ())
            self.assertEqual(len(collect(root / "output/artifacts", plan)), 2)
            report = json.loads((root / "output/packaging-report.json").read_text())
            self.assertEqual([row["source_run"] for row in report], ["1", "2"])
            self.assertTrue(
                all(row["input_sha256"] == row["output_sha256"] for row in report)
            )
