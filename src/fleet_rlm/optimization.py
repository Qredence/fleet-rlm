"""Deterministic dataset and artifact-promotion primitives for optimization."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import dspy


class IsolatedFleetProgram(dspy.Module):
    """Expose candidate predictors while giving every rollout a private runtime."""

    def __init__(self, program: dspy.Module, engine_factory: Any):
        super().__init__()
        self.program = program
        self.engine_factory = engine_factory

    def forward(self, **inputs: Any) -> dspy.Prediction:
        engine = self.engine_factory(dspy.Example(**inputs), self.program)
        try:
            return engine(
                **{
                    name: value
                    for name, value in inputs.items()
                    if name
                    in {"workspace_manifest", "skills_catalog", "memory_summary", "task", "session_history", "context"}
                }
            )
        finally:
            engine.sandbox_mgr.shutdown()
            artifact_path = getattr(engine, "_optimization_artifact_path", None)
            if artifact_path:
                Path(artifact_path).unlink(missing_ok=True)


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def grouped_split(
    examples: list[Any], *, seed: int = 0, train_ratio: float = 0.6, dev_ratio: float = 0.2
) -> dict[str, list[Any]]:
    """Split by source lineage, never allowing a source across partitions.

    Task text is deliberately not part of the grouping key: different tasks
    from one originating source are still correlated data and must stay
    together. Records without a source_group are treated as one conservative
    unknown source rather than being split by task.
    """
    if not 0 < train_ratio < 1 or not 0 < dev_ratio < 1 or train_ratio + dev_ratio >= 1:
        raise ValueError("train_ratio and dev_ratio must be positive and sum to less than one")
    groups: dict[str, list[Any]] = {}
    for example in examples:
        inputs = getattr(example, "inputs", lambda: {})()
        lineage = str(inputs.get("source_group", "")).strip()
        if not lineage:
            lineage = str(getattr(example, "source_group", "")).strip()
        if not lineage:
            raise ValueError("Every curated example must have a source_group lineage")
        group = hashlib.sha256(lineage.encode()).hexdigest()
        groups.setdefault(group, []).append(example)
    ordered = sorted(groups.items(), key=lambda pair: hashlib.sha256(f"{seed}:{pair[0]}".encode()).hexdigest())
    if len(ordered) < 3:
        raise ValueError("At least three distinct task/source groups are required for train/dev/holdout")
    count = len(ordered)
    train_n = max(1, min(count - 2, int(count * train_ratio)))
    dev_n = max(1, min(count - train_n - 1, int(count * dev_ratio)))
    partitions = {"train": [], "dev": [], "holdout": []}
    for index, (_, members) in enumerate(ordered):
        key = "train" if index < train_n else "dev" if index < train_n + dev_n else "holdout"
        partitions[key].extend(members)
    return partitions


def isolated_pair(baseline: Any, candidate: Any) -> tuple[Any, Any]:
    """Return deep independent copies; refuse if copies still alias mutable state."""
    left, right = copy.deepcopy(baseline), copy.deepcopy(candidate)
    if left is right or right is baseline or left is candidate:
        raise ValueError("baseline and candidate could not be isolated")
    return left, right


@dataclass(frozen=True)
class EvaluationGate:
    passed: bool
    reasons: tuple[str, ...]


def promotion_gate(
    baseline: Mapping[str, float | None],
    candidate: Mapping[str, float | None],
    *,
    quality_metrics: tuple[str, ...],
    tolerance: float = 0.0,
    latency_ceiling: float | None = None,
    security_violations: int | None = None,
    golden_regression: bool | None = None,
) -> EvaluationGate:
    reasons: list[str] = []
    for metric in quality_metrics:
        before, after = baseline.get(metric), candidate.get(metric)
        if (
            before is None
            or after is None
            or isinstance(before, bool)
            or isinstance(after, bool)
            or not isinstance(before, (int, float))
            or not isinstance(after, (int, float))
            or not math.isfinite(before)
            or not math.isfinite(after)
        ):
            reasons.append(f"missing metric: {metric}")
        elif after < before - tolerance:
            reasons.append(f"quality regression: {metric}")
    latency = candidate.get("avg_turn_latency_sec")
    if latency_ceiling is not None and (
        latency is None
        or isinstance(latency, bool)
        or not isinstance(latency, (int, float))
        or not math.isfinite(latency)
        or latency > latency_ceiling
    ):
        reasons.append("latency ceiling missing or exceeded")
    if (
        security_violations is None
        or isinstance(security_violations, bool)
        or not isinstance(security_violations, int)
        or security_violations < 0
        or security_violations
    ):
        reasons.append("security/isolation/budget evidence missing or violated")
    if golden_regression is None or golden_regression:
        reasons.append("golden artifact evidence missing or regressed")
    return EvaluationGate(not reasons, tuple(reasons))


@dataclass(frozen=True)
class ArtifactManifest:
    version: str
    digest: str
    dataset_lineage: Mapping[str, Any]
    evaluation: Mapping[str, Any]
    calibration: Mapping[str, Any] = field(default_factory=lambda: {"status": "not_run"})
    dependencies: Mapping[str, str] = field(default_factory=dict)


def write_manifest(path: Path, manifest: ArtifactManifest) -> None:
    _atomic_write(path, json.dumps(asdict(manifest), sort_keys=True, indent=2).encode())


def promote_candidate(
    candidate_path: Path,
    active_pointer: Path,
    manifest: ArtifactManifest,
    gate: EvaluationGate,
    *,
    authorized: bool = False,
) -> None:
    """Atomically update a pointer only after an explicit authorized pass."""
    if not authorized:
        raise PermissionError("promotion requires explicit authorization")
    if not gate.passed:
        raise ValueError("candidate failed promotion gates: " + "; ".join(gate.reasons))
    actual = digest_bytes(candidate_path.read_bytes())
    if actual != manifest.digest:
        raise ValueError("candidate digest does not match its manifest")
    _atomic_write(active_pointer, json.dumps({"version": manifest.version, "digest": actual}).encode())


def verify_active(pointer: Path, artifact_dir: Path) -> Path:
    data = json.loads(pointer.read_bytes())
    version = data["version"]
    if not isinstance(version, str) or Path(version).name != version:
        raise ValueError("active artifact version is invalid")
    artifact = artifact_dir / f"{version}.json"
    if digest_bytes(artifact.read_bytes()) != data["digest"]:
        raise ValueError("active artifact integrity check failed")
    return artifact


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
