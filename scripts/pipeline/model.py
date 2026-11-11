"""Represent resolved sources and independent Android build targets."""

import json
import re
import shlex
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

from scripts.build.android_python import AndroidABI, PythonVersion
from scripts.build.build_tools import BuildTool
from scripts.build.maturin_settings import maturin_options
from scripts.build.native_model import NativeLibrary
from scripts.build.pep517_settings import workspace_settings
from scripts.pipeline.compatibility import UpstreamUnsupported, upstream_unsupported
from scripts.recipes.model import (
    Backend,
    ConfigSetting,
    EnvironmentVariable,
    Publication,
)
from scripts.recipes.plan import artifact_prefix


class Builder(StrEnum):
    MATURIN = "maturin"
    CIBUILDWHEEL = "cibuildwheel"
    CHAQUOPY = "chaquopy"


@dataclass(frozen=True)
class GitSource:
    repository: str
    ref: str
    sha: str
    directory: str
    backend: Backend
    requires_python: str = ""
    kind: Literal["git"] = field(init=False, default="git")


@dataclass(frozen=True)
class SdistSource:
    package: str
    version: str
    url: str
    sha256: str
    filename: str
    directory: str
    backend: Backend
    requires_python: str = ""
    kind: Literal["pypi-sdist"] = field(init=False, default="pypi-sdist")


Source = GitSource | SdistSource


@dataclass(frozen=True)
class Target:
    library: str
    source: Source
    python: PythonVersion
    abi: AndroidABI
    api: int
    environment: tuple[EnvironmentVariable, ...] = ()
    native_dependencies: tuple[NativeLibrary, ...] = ()
    config_settings: tuple[ConfigSetting, ...] = ()
    build_tools: tuple[BuildTool, ...] = ()

    def to_dict(self) -> dict:
        result = {
            **asdict(self),
            "environment": [asdict(variable) for variable in self.environment],
            "native_dependencies": list(self.native_dependencies),
            "config_settings": [asdict(setting) for setting in self.config_settings],
        }
        del result["build_tools"]
        if self.build_tools:
            result["build_tools"] = list(self.build_tools)
        return result

    @property
    def builder(self) -> Builder:
        if self.source.backend == Backend.MATURIN:
            maturin_options(self.config_settings)
            return Builder.MATURIN
        if self.python in {
            PythonVersion.PY310,
            PythonVersion.PY311,
            PythonVersion.PY312,
        }:
            return Builder.CHAQUOPY
        return Builder.CIBUILDWHEEL

    @property
    def artifact(self) -> str:
        return f"{artifact_prefix(self.library, self.python)}-{self.abi.value}"

    def row(self) -> dict:
        return {
            **self.to_dict(),
            "builder": self.builder.value,
            "artifact": self.artifact,
            "triplet": self.abi.triplet,
            "cibw_arch": self.abi.value.replace("-", "_"),
            "cibw_config_settings": " ".join(
                f"{setting.name}={shlex.quote(setting.value)}"
                for setting in workspace_settings(self.config_settings, Path.cwd())
            ),
            "cibw_environment": " ".join(
                f"{variable.name}={shlex.quote(variable.value)}"
                for variable in self.environment
            ),
        }


@dataclass(frozen=True)
class UnsupportedPython:
    library: str
    python: PythonVersion
    requires_python: str
    reason: Literal["requires-python"] = field(init=False, default="requires-python")


@dataclass(frozen=True)
class BuildFingerprint:
    artifact: str
    sha256: str


@dataclass(frozen=True)
class AlreadyPublished:
    library: str
    python: PythonVersion
    abi: AndroidABI
    fingerprint: str
    wheel: str
    blob: str
    reason: Literal["already-published"] = field(
        init=False, default="already-published"
    )


@dataclass(frozen=True)
class Plan:
    publication: Publication
    targets: tuple[Target, ...]
    skipped: tuple[UnsupportedPython | UpstreamUnsupported | AlreadyPublished, ...] = ()
    fingerprints: tuple[BuildFingerprint, ...] = ()

    def write(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "publication": self.publication,
                    "targets": [t.to_dict() for t in self.targets],
                    "skipped": [asdict(s) for s in self.skipped],
                    "fingerprints": [asdict(s) for s in self.fingerprints],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def target(self, artifact: str) -> Target:
        return next(t for t in self.targets if t.artifact == artifact)


def load_plan(path: Path) -> Plan:
    data = json.loads(path.read_text(encoding="utf-8"))
    targets = []
    for row in data["targets"]:
        fields = dict(row["source"])
        kind = fields.pop("kind")
        fields["backend"] = Backend(fields["backend"])
        match kind:
            case "git":
                source = GitSource(**fields)
                if not re.fullmatch(r"[a-f0-9]{40}", source.sha):
                    raise ValueError("构建计划必须使用完整提交 SHA")
            case "pypi-sdist":
                source = SdistSource(**fields)
                if not re.fullmatch(r"[a-f0-9]{64}", source.sha256):
                    raise ValueError("源码分发包必须使用完整 SHA-256")
            case _:
                raise ValueError(f"未知源码类型：{kind}")
        targets.append(
            Target(
                row["library"],
                source,
                PythonVersion(row["python"]),
                AndroidABI(row["abi"]),
                row["api"],
                tuple(EnvironmentVariable(**v) for v in row["environment"]),
                tuple(NativeLibrary(v) for v in row["native_dependencies"]),
                tuple(ConfigSetting(**v) for v in row["config_settings"]),
                tuple(BuildTool(value) for value in row.get("build_tools", [])),
            )
        )
    skipped = []
    for row in data["skipped"]:
        fields = dict(row)
        match fields.pop("reason"):
            case "requires-python":
                skipped.append(
                    UnsupportedPython(
                        fields["library"],
                        PythonVersion(fields["python"]),
                        fields["requires_python"],
                    )
                )
            case "upstream-incompatibility":
                skipped.append(upstream_unsupported(fields))
            case "already-published":
                skipped.append(
                    AlreadyPublished(
                        fields["library"],
                        PythonVersion(fields["python"]),
                        AndroidABI(fields["abi"]),
                        fields["fingerprint"],
                        fields["wheel"],
                        fields["blob"],
                    )
                )
            case reason:
                raise ValueError(f"未知跳过原因：{reason}")
    fingerprints = tuple(
        BuildFingerprint(**row) for row in data.get("fingerprints", [])
    )
    plan = Plan(
        Publication(data["publication"]), tuple(targets), tuple(skipped), fingerprints
    )
    if len({t.artifact for t in plan.targets}) != len(plan.targets):
        raise ValueError("构建计划存在重复目标")
    if fingerprints and (
        {f.artifact for f in fingerprints} != {t.artifact for t in targets}
        or len(fingerprints) != len(targets)
        or any(not re.fullmatch(r"[a-f0-9]{64}", f.sha256) for f in fingerprints)
    ):
        raise ValueError("构建指纹必须完整且唯一地覆盖待构建目标")
    return plan
