"""Read and write successful-build receipts bound to immutable Git wheel blobs."""

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from scripts.pipeline.model import Plan

RECEIPTS = ".build-records"


@dataclass(frozen=True)
class PublishedWheel:
    artifact: str
    fingerprint: str
    wheel: str
    blob: str


@dataclass(frozen=True)
class PublishedStore:
    revision: str
    blobs: dict[str, str]
    records: dict[str, PublishedWheel]

    def matches(self, artifact: str, fingerprint: str) -> bool:
        record = self.records.get(artifact)
        return (
            record is not None
            and record.fingerprint == fingerprint
            and self.blobs.get(record.wheel) == record.blob
        )


def read_store(checkout: Path) -> PublishedStore:
    """Read metadata only; wheel existence and bytes are bound by the Git tree."""
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
    ).strip()
    tree = subprocess.check_output(["git", "ls-tree", "-rz", revision], cwd=checkout)
    blobs = {}
    receipts = {}
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        attributes, raw_path = entry.split(b"\t", 1)
        mode, kind, blob = attributes.decode().split()
        path = raw_path.decode()
        if mode not in {"100644", "100755"} or kind != "blob":
            continue
        blobs[path] = blob
        if path.startswith(RECEIPTS + "/") and path.endswith(".json"):
            if len(PurePosixPath(path).parts) != 2:
                raise ValueError(f"构建记录必须位于固定目录：{path}")
            receipts[path] = blob
    content = (
        subprocess.check_output(
            ["git", "cat-file", "--batch"],
            input="".join(blob + "\n" for blob in receipts.values()).encode(),
            cwd=checkout,
        )
        if receipts
        else b""
    )
    records = {}
    cursor = 0
    for path, blob in receipts.items():
        end = content.index(b"\n", cursor)
        actual_blob, kind, size = content[cursor:end].decode().split()
        if actual_blob != blob or kind != "blob":
            raise ValueError("构建记录 Git 对象不匹配")
        cursor = end + 1
        row = json.loads(content[cursor : cursor + int(size)])
        cursor += int(size) + 1
        if row.pop("schema") != 1:
            raise ValueError("不支持的构建记录版本")
        record = PublishedWheel(**row)
        wheel = PurePosixPath(record.wheel)
        if (
            path != f"{RECEIPTS}/{record.artifact}.json"
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", record.artifact)
            or not re.fullmatch(r"[a-f0-9]{64}", record.fingerprint)
            or not re.fullmatch(r"[a-f0-9]{40}", record.blob)
            or wheel.is_absolute()
            or len(wheel.parts) != 2
            or ".." in wheel.parts
            or "\\" in record.wheel
            or not record.wheel.endswith(".whl")
        ):
            raise ValueError(f"无效构建记录：{path}")
        records[record.artifact] = record
    return PublishedStore(revision, blobs, records)


def wheel_blob(path: Path) -> str:
    """Compute the Git blob identity without copying or publishing wheel bytes."""
    result = hashlib.sha1(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def write_receipts(
    plan: Plan, artifacts: Path, wheels: dict[str, Path], destination: Path
) -> dict[str, Path]:
    result = {}
    by_path = {path.resolve(): name for name, path in wheels.items()}
    for identity in plan.fingerprints:
        target = plan.target(identity.artifact)
        folder = artifacts / target.artifact
        manifest = json.loads((folder / "manifest.json").read_text())
        if manifest.get("fingerprint") != identity.sha256:
            raise ValueError(f"产物构建指纹与计划不一致：{target.artifact}")
        wheel = folder / manifest["wheel"]
        row = PublishedWheel(
            target.artifact,
            identity.sha256,
            by_path[wheel.resolve()],
            wheel_blob(wheel),
        )
        path = destination / f"{target.artifact}.json"
        path.write_text(json.dumps({"schema": 1, **asdict(row)}, indent=2) + "\n")
        result[f"{RECEIPTS}/{path.name}"] = path
    return result
