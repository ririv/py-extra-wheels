"""Record and verify wheel artifacts against the complete resolved plan."""

import hashlib
import json
from pathlib import Path

from wheel.cli.tags import tags

from scripts.build.wheel_licenses import attach_bundled_licenses
from scripts.pipeline.model import BuildFingerprint, Plan, Target
from scripts.pipeline.wheel import normalized, verify_wheel


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def record(
    directory: Path, target: Target, identity: BuildFingerprint | None = None
) -> None:
    wheels = sorted(directory.glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError(
            f"每个目标必须生成一个 wheel，实际为 {len(wheels)}：{directory}"
        )
    wheel = wheels[0]
    _, abi, platform = wheel.stem.rsplit("-", 2)
    if abi == "abi3" or platform == "any":
        # 稳定 ABI 与纯 Python 产物跨版本同名，用构建号区分实际构建来源。
        wheel = wheel.parent / tags(
            str(wheel),
            build_tag=target.python.value.replace(".", "")
            + (target.abi.value.replace("-", "_") if platform == "any" else ""),
            remove=True,
        )
    attach_bundled_licenses(wheel, target)
    verify_wheel(wheel, target)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "target": target.to_dict(),
                "wheel": wheel.name,
                "sha256": digest(wheel),
                **({"fingerprint": identity.sha256} if identity is not None else {}),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def collect(directory: Path, plan: Plan) -> dict[str, Path]:
    expected = {t.artifact for t in plan.targets}
    actual = {p.name for p in directory.iterdir()}
    if not expected or actual != expected:
        raise ValueError(
            f"产物集合不完整：缺少 {sorted(expected - actual)}，多余 {sorted(actual - expected)}"
        )
    result = {}
    identities = {identity.artifact: identity for identity in plan.fingerprints}
    for target in plan.targets:
        artifact = directory / target.artifact
        wheel = artifact_wheel(artifact, target)
        if identity := identities.get(target.artifact):
            manifest = json.loads((artifact / "manifest.json").read_text())
            if manifest.get("fingerprint") != identity.sha256:
                raise ValueError(f"产物构建指纹与计划不一致：{target.artifact}")
        verify_wheel(wheel, target)
        key = f"{normalized(target.library)}/{wheel.name}"
        if key in result and digest(result[key]) != digest(wheel):
            raise ValueError(f"同名 wheel 内容冲突：{key}")
        result[key] = wheel
    return result


def artifact_wheel(artifact: Path, target: Target) -> Path:
    """Verify the immutable source manifest before inspecting or repackaging bytes."""
    manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
    if manifest["target"] != target.to_dict():
        raise ValueError(f"产物来源与构建计划不匹配：{target.artifact}")
    name = manifest["wheel"]
    if Path(name).name != name or not name.endswith(".whl"):
        raise ValueError("无效的 wheel 文件名")
    if {p.name for p in artifact.iterdir()} != {"manifest.json", name}:
        raise ValueError(f"产物目录存在未声明文件：{artifact}")
    wheel = artifact / name
    if digest(wheel) != manifest["sha256"]:
        raise ValueError(f"wheel 哈希校验失败：{wheel}")
    return wheel
