"""Translate Actions events into pinned, independently executable targets."""

import json
import subprocess
from pathlib import Path

from packaging.specifiers import SpecifierSet

from scripts.build.android_python import AndroidABI, PythonVersion
from scripts.pipeline.compatibility import (
    GitCommit,
    SdistArchive,
    load_incompatibilities,
)
from scripts.pipeline.model import (
    AlreadyPublished,
    GitSource,
    Plan,
    Target,
    UnsupportedPython,
)
from scripts.pipeline.source import resolve_legacy_sdist, resolve_source
from scripts.recipes.load import load_recipes
from scripts.recipes.model import (
    Backend,
    ConfigSetting,
    GitLocation,
    Publication,
    RecipeBuilds,
)
from scripts.recipes.plan import (
    affected_recipes,
    build_jobs,
    changed_paths,
)
from scripts.recipes.request import requested_builds


def recipe_plan(event_name: str, event: dict, root: Path) -> Plan:
    recipes = load_recipes(root / "recipes")
    if event_name == "push":
        before = event["before"]
        if before == "0" * 40:
            selected = recipes
        else:
            subprocess.run(
                ["git", "fetch", "--no-tags", "--depth=1", "origin", before],
                cwd=root,
                check=True,
            )
            selected = affected_recipes(
                recipes, changed_paths(root, before, event["after"])
            )
        request = RecipeBuilds(selected)
        publication = Publication.PUBLISH
    elif event_name == "workflow_dispatch":
        inputs = event["inputs"]
        request = requested_builds(
            recipes,
            inputs["recipes"],
            inputs["python_versions"],
            inputs.get("version_tag", ""),
        )
        publication = Publication(inputs["publication"])
    else:
        raise ValueError(f"不支持的事件：{event_name}")
    incompatibilities = {
        (e.library, e.python, e.source): e
        for e in load_incompatibilities(root / "build-compatibility.toml")
    }
    sources = {}
    targets = []
    skipped = []
    for job in build_jobs(request):
        recipe = job.recipe
        key = (recipe.source, recipe.directory)
        if key not in sources:
            sources[key] = (
                resolve_legacy_sdist(recipe.source, recipe.directory)
                if recipe.backend == Backend.SETUPTOOLS_LEGACY
                else resolve_source(recipe.source, recipe.directory)
            )
        source = sources[key]
        if source.backend != recipe.backend:
            raise ValueError(f"{recipe.name} 后端与 recipe 不一致：{source.backend}")
        if not SpecifierSet(source.requires_python).contains(job.python.value):
            skipped.append(
                UnsupportedPython(recipe.name, job.python, source.requires_python)
            )
            continue
        identity = (
            GitCommit(source.sha)
            if isinstance(source, GitSource)
            else SdistArchive(source.sha256)
        )
        if entry := incompatibilities.get((recipe.name, job.python, identity)):
            skipped.append(entry)
            continue
        targets.extend(
            Target(
                recipe.name,
                source,
                job.python,
                abi,
                recipe.android.api,
                recipe.environment,
                recipe.native_dependencies,
                tuple(
                    ConfigSetting(
                        s.name,
                        s.value.replace("{python}", job.python.value).replace(
                            "{abi}", abi.value
                        ),
                    )
                    for s in recipe.config_settings
                ),
                recipe.build_tools,
            )
            for abi in recipe.android.abis.values
        )
    return Plan(publication, tuple(targets), tuple(skipped))


def manual_plan(event: dict) -> Plan:
    inputs = event["inputs"]
    source = resolve_source(
        GitLocation(inputs["git_repository"], inputs.get("version_tag", "")),
        inputs["package_dir"],
    )
    api = int(inputs["android_api"])
    if api < 24:
        raise ValueError("Android API 必须至少为 24")
    python = PythonVersion(inputs["target_python_version"])
    if not SpecifierSet(source.requires_python).contains(python.value):
        return Plan(
            Publication(inputs["publication"]),
            (),
            (
                UnsupportedPython(
                    inputs["library_name"], python, source.requires_python
                ),
            ),
        )
    return Plan(
        Publication(inputs["publication"]),
        tuple(
            Target(
                inputs["library_name"],
                source,
                python,
                abi,
                api,
            )
            for abi in AndroidABI
        ),
    )


def write_actions_plan(plan: Plan, path: Path, output: Path) -> None:
    plan.write(path)
    with output.open("a", encoding="utf-8") as stream:
        stream.write(
            f"matrix={json.dumps({'include': [{'batch': i} for i in range((len(plan.targets) + BATCH_SIZE - 1) // BATCH_SIZE)]}, separators=(',', ':'))}\n"
        )
        stream.write(f"has_jobs={str(bool(plan.targets)).lower()}\n")
        stream.write(
            f"has_reused={str(any(isinstance(s, AlreadyPublished) for s in plan.skipped)).lower()}\n"
        )
        stream.write(f"publication={plan.publication.value}\n")


BATCH_SIZE = 96


def write_batch(plan: Plan, batch: int, output: Path) -> None:
    """Emit one bounded matrix without copying the plan into job outputs."""
    start = batch * BATCH_SIZE
    targets = plan.targets[start : start + BATCH_SIZE]
    if batch < 0 or not targets:
        raise ValueError(f"不存在构建批次：{batch}")
    with output.open("a", encoding="utf-8") as stream:
        stream.write(
            f"matrix={json.dumps({'include': [t.row() for t in targets]}, separators=(',', ':'))}\n"
        )
