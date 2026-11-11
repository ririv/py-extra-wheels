"""Repackage complete, source-verified cohorts without repeating native builds."""

import argparse
import fnmatch
import json
import re
import shutil
import subprocess
from dataclasses import asdict, replace
from pathlib import Path

from scripts.build.wheel_rpath import normalize_rpaths
from scripts.pipeline.artifacts import artifact_wheel, collect, digest, record
from scripts.pipeline.model import Plan, load_plan
from scripts.recipes.model import Publication


def select_plan(plan: Plan, names: list[str]) -> Plan:
    available = {target.library for target in plan.targets} | {
        entry.library for entry in plan.skipped
    }
    if (
        not names
        or any(not isinstance(name, str) for name in names)
        or len(set(names)) != len(names)
        or set(names) - available
    ):
        raise ValueError("重打包必须显式选择原计划中存在且不重复的包")
    return replace(
        plan,
        targets=tuple(target for target in plan.targets if target.library in names),
        skipped=tuple(entry for entry in plan.skipped if entry.library in names),
        fingerprints=tuple(
            identity
            for identity in plan.fingerprints
            if plan.target(identity.artifact).library in names
        ),
    )


def repackage(inputs: tuple[tuple[str, Path, Plan], ...], output: Path) -> None:
    output.mkdir()
    artifacts = output / "artifacts"
    artifacts.mkdir()
    targets = {}
    sources = {}
    for run, directory, plan in inputs:
        for target in plan.targets:
            name = target.artifact
            if name in targets and targets[name] != target:
                raise ValueError(f"输入构建计划的目标不一致：{name}")
            targets[name] = target
            folder = directory / name
            if folder.is_dir():
                wheel = artifact_wheel(folder, target)
                if name in sources and digest(sources[name][1]) != digest(wheel):
                    raise ValueError(f"同一目标存在不同输入产物：{name}")
                sources[name] = (run, wheel)
        extra = {p.name for p in directory.iterdir()} - {
            t.artifact for t in plan.targets
        }
        if extra:
            raise ValueError(f"输入包含计划外产物：{sorted(extra)}")
    if not targets or set(sources) != set(targets):
        raise ValueError(f"输入缺少构建目标：{sorted(set(targets) - set(sources))}")
    report = []
    for name, target in targets.items():
        run, source = sources[name]
        folder = artifacts / name
        shutil.copytree(source.parent, folder)
        wheel = folder / source.name
        changes = normalize_rpaths(wheel)
        record(folder, target)
        report.append(
            {
                "artifact": name,
                "source_run": run,
                "input_sha256": digest(source),
                "output_sha256": digest(wheel),
                "rpath_changes": [asdict(change) for change in changes],
            }
        )
    skipped = tuple(
        dict.fromkeys(
            entry for _, _, source_plan in inputs for entry in source_plan.skipped
        )
    )
    plan = Plan(Publication.BUILD, tuple(targets.values()), skipped)
    plan.write(output / "build-plan.json")
    collect(artifacts, plan)
    (output / "packaging-report.json").write_text(json.dumps(report, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recipe-selections", default="{}")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]+(?:,[0-9]+)*", args.runs):
        raise ValueError("构建编号必须是逗号分隔的数字")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        raise ValueError("无效的仓库标识")
    selections = json.loads(args.recipe_selections)
    if (
        not isinstance(selections, dict)
        or set(selections) - set(args.runs.split(","))
        or any(not isinstance(names, list) for names in selections.values())
    ):
        raise ValueError("包选择必须是运行编号到包名列表的 JSON 对象")
    inputs = []
    for run in args.runs.split(","):
        root = args.output.parent / ("repackage-input-" + run)
        subprocess.run(
            [
                "gh",
                "run",
                "download",
                run,
                "--repo",
                args.repository,
                "--name",
                "build-plan",
                "--dir",
                str(root / "plan"),
            ],
            check=True,
        )
        plan = load_plan(root / "plan/build-plan.json")
        original = {target.artifact for target in plan.targets}
        if run in selections:
            plan = select_plan(plan, selections[run])
        expected = {target.artifact for target in plan.targets}
        available = set(
            subprocess.check_output(
                [
                    "gh",
                    "api",
                    "--paginate",
                    f"repos/{args.repository}/actions/runs/{run}/artifacts?per_page=100",
                    "--jq",
                    ".artifacts[].name",
                ],
                text=True,
            ).splitlines()
        )
        extra = {
            name
            for name in available
            if fnmatch.fnmatchcase(name, "*-android-*-wheel-*")
        } - original
        if extra:
            raise ValueError(f"运行包含计划外 wheel 产物：{sorted(extra)}")
        directory = root / "artifacts"
        directory.mkdir()
        for name in sorted(expected & available):
            subprocess.run(
                [
                    "gh",
                    "run",
                    "download",
                    run,
                    "--repo",
                    args.repository,
                    "--name",
                    name,
                    "--dir",
                    str(directory / name),
                ],
                check=True,
            )
        inputs.append((run, directory, plan))
    repackage(tuple(inputs), args.output)


if __name__ == "__main__":
    main()
