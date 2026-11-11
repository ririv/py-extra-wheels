"""Run one explicit stage of the build pipeline."""

import argparse
import json
import os
import tempfile
from pathlib import Path

from scripts.pipeline.artifacts import collect, record
from scripts.pipeline.fingerprint import fingerprint
from scripts.pipeline.incremental import RebuildMode, incremental_plan
from scripts.pipeline.model import AlreadyPublished, GitSource, SdistSource, load_plan
from scripts.pipeline.notifications import notify_index, notify_legacy
from scripts.pipeline.planning import (
    manual_plan,
    recipe_plan,
    write_actions_plan,
    write_batch,
)
from scripts.pipeline.publish import publish
from scripts.pipeline.published import read_store, write_receipts
from scripts.pipeline.source import prepare_sdist, verify_checkout
from scripts.recipes.model import Publication


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("notify-index", "notify-legacy"):
        command = commands.add_parser(name)
        command.add_argument("--repository", required=True)
    local = commands.add_parser("plan-local")
    local.add_argument("--recipes", required=True)
    local.add_argument("--python-versions", default="recipe")
    local.add_argument("--version-tag", default="")
    local.add_argument("--publication", type=Publication, default=Publication.BUILD)
    local.add_argument("--output", type=Path, default=Path("build-plan.json"))
    local.add_argument("--github-output", type=Path, required=True)
    for name in ("plan", "plan-source"):
        command = commands.add_parser(name)
        command.add_argument("--event", type=Path, required=True)
        command.add_argument("--event-name", required=True)
        command.add_argument("--github-output", type=Path, required=True)
        command.add_argument("--output", type=Path, default=Path("build-plan.json"))
        if name == "plan":
            command.add_argument("--published-store", type=Path, required=True)
    batch = commands.add_parser("matrix-batch")
    batch.add_argument("--plan", type=Path, default=Path("build-plan.json"))
    batch.add_argument("--batch", type=int, required=True)
    batch.add_argument("--github-output", type=Path, required=True)
    environment = commands.add_parser("build-environment")
    environment.add_argument("--plan", type=Path, default=Path("build-plan.json"))
    environment.add_argument("--artifact", required=True)
    environment.add_argument("--output", type=Path, required=True)
    for name in ("prepare-source", "record", "publish"):
        command = commands.add_parser(name)
        command.add_argument("--plan", type=Path, default=Path("build-plan.json"))
        if name == "publish":
            command.add_argument("--artifacts", type=Path, required=True)
        else:
            command.add_argument("--artifact", required=True)
            command.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if args.command in {"notify-index", "notify-legacy"}:
        print(
            notify_index(args.repository)
            if args.command == "notify-index"
            else notify_legacy(args.repository)
        )
        return
    if args.command in {"plan", "plan-source", "plan-local"}:
        event = (
            {
                "inputs": {
                    "recipes": args.recipes,
                    "python_versions": args.python_versions,
                    "version_tag": args.version_tag,
                    "publication": args.publication.value,
                }
            }
            if args.command == "plan-local"
            else json.loads(args.event.read_text(encoding="utf-8"))
        )
        plan = (
            manual_plan(event)
            if args.command == "plan-source"
            else recipe_plan(
                "workflow_dispatch"
                if args.command == "plan-local"
                else args.event_name,
                event,
                Path.cwd(),
            )
        )
        if args.command == "plan":
            mode = (
                RebuildMode.FORCE
                if plan.publication == Publication.BUILD
                else RebuildMode(event.get("inputs", {}).get("rebuild", "changed"))
            )
            store = read_store(args.published_store)
            plan = incremental_plan(plan, Path.cwd(), store, mode)
            reused = sum(isinstance(s, AlreadyPublished) for s in plan.skipped)
            if "GITHUB_STEP_SUMMARY" in os.environ:
                with Path(os.environ["GITHUB_STEP_SUMMARY"]).open(
                    "a", encoding="utf-8"
                ) as stream:
                    stream.write(
                        "## 增量构建计划\n\n"
                        f"- 模式：`{mode.value}`\n"
                        f"- 待构建：{len(plan.targets)}\n"
                        f"- 已发布且指纹一致：{reused}\n"
                        f"- 上游不支持：{len(plan.skipped) - reused}\n"
                        f"- 核对的 wheels 提交：`{store.revision}`\n\n"
                        "每个跳过目标的指纹、wheel 路径和 Git blob 见 build-plan.json。\n"
                    )
        write_actions_plan(plan, args.output, args.github_output)
        print(f"待构建 {len(plan.targets)} 个目标；跳过 {len(plan.skipped)} 个目标")
        print(args.output.read_text(encoding="utf-8"))
        return
    plan = load_plan(args.plan)
    if args.command == "matrix-batch":
        write_batch(plan, args.batch, args.github_output)
    elif args.command == "build-environment":
        with args.output.open("a", encoding="utf-8") as stream:
            for variable in plan.target(args.artifact).environment:
                stream.write(f"{variable.name}={variable.value}\n")
    elif args.command == "prepare-source":
        source = plan.target(args.artifact).source
        match source:
            case GitSource():
                verify_checkout(source, args.directory)
            case SdistSource():
                prepare_sdist(source, args.directory)
    elif args.command == "record":
        target = plan.target(args.artifact)
        identity = next(
            (f for f in plan.fingerprints if f.artifact == args.artifact), None
        )
        if identity is not None and fingerprint(target, Path.cwd()) != identity.sha256:
            raise ValueError("当前构建代码与规划时的构建指纹不一致")
        record(args.directory, target, identity)
    else:
        if plan.publication != Publication.PUBLISH:
            raise ValueError("当前计划只允许生成 Actions 产物，禁止发布")
        wheels = collect(args.artifacts, plan)
        with tempfile.TemporaryDirectory(prefix="wheel-receipts-") as temporary:
            receipts = write_receipts(plan, args.artifacts, wheels, Path(temporary))
            result = publish(
                Path.cwd(),
                {**wheels, **receipts},
                f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
            )
        print(result)


if __name__ == "__main__":
    main()
