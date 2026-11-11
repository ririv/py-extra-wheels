"""Remove verified published targets before expanding Actions matrix batches."""

from dataclasses import replace
from enum import StrEnum
from pathlib import Path

from scripts.pipeline.fingerprint import Fingerprinter
from scripts.pipeline.model import AlreadyPublished, BuildFingerprint, Plan
from scripts.pipeline.published import PublishedStore


class RebuildMode(StrEnum):
    CHANGED = "changed"
    FORCE = "force"


def incremental_plan(
    plan: Plan, root: Path, store: PublishedStore, mode: RebuildMode
) -> Plan:
    targets = []
    fingerprints = []
    skipped = list(plan.skipped)
    identify = Fingerprinter(root)
    for target in plan.targets:
        key = identify(target)
        if mode == RebuildMode.CHANGED and store.matches(target.artifact, key):
            record = store.records[target.artifact]
            skipped.append(
                AlreadyPublished(
                    target.library,
                    target.python,
                    target.abi,
                    key,
                    record.wheel,
                    record.blob,
                )
            )
        else:
            targets.append(target)
            fingerprints.append(BuildFingerprint(target.artifact, key))
    return replace(
        plan,
        targets=tuple(targets),
        skipped=tuple(skipped),
        fingerprints=tuple(fingerprints),
    )
