"""Compiled-program artifacts: isolated rollouts, manifests, promotion gates, and the single loader."""

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
            artifact_path = getattr(engine, "optimization_artifact_path", None)
            if artifact_path:
                Path(artifact_path).unlink(missing_ok=True)


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


class CompiledProgramFile:
    """The one path from a compiled-program file to a live engine: bounded, atomic, rolled back on failure.

    Accepts artifacts saved from the engine itself and from the IsolatedFleetProgram wrapper
    (whose state keys carry a ``program.`` prefix).
    """

    MAX_BYTES = 10_000_000

    def __init__(self, path: str | None) -> None:
        self.path = path
        self.digest = ""
        self._stat: tuple[int, int] | None = None

    def load_initial(self, program: dspy.Module) -> None:
        if self.path and os.path.lexists(self.path):
            self._apply(program, self._read(), persist=False)

    def reload_if_changed(self, program: dspy.Module) -> None:
        if not self.path or not os.path.lexists(self.path):
            return
        stat = self._regular_stat(self.path)
        if (stat.st_mtime_ns, stat.st_size) != self._stat:
            self._apply(program, self._read(), persist=False)

    def install(self, program: dspy.Module, content: bytes) -> bool:
        if len(content) > self.MAX_BYTES:
            raise ValueError("Compiled program exceeds the 10 MB size limit")
        if hashlib.sha256(content).hexdigest() == self.digest:
            return False
        self._apply(program, content, persist=True)
        return True

    def _read(self) -> bytes:
        assert self.path is not None
        if self._regular_stat(self.path).st_size > self.MAX_BYTES:
            raise ValueError("Compiled program exceeds the 10 MB size limit")
        return Path(self.path).read_bytes()

    @staticmethod
    def _regular_stat(path: str) -> os.stat_result:
        if os.path.islink(path) or not os.path.isfile(path):
            raise ValueError("Compiled program path must be a regular file")
        return os.stat(path)

    def _apply(self, program: dspy.Module, content: bytes, *, persist: bool) -> None:
        state = json.loads(content)
        if not isinstance(state, dict):
            raise ValueError("Compiled program must be a JSON object")
        if state and all(key.startswith("program.") or key == "metadata" for key in state):
            state = {key.removeprefix("program."): value for key, value in state.items()}
        target = os.path.abspath(self.path) if persist and self.path else None
        if target:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.islink(target):
                raise ValueError("Compiled program path may not be a symbolic link")
        program.load_state(state)  # DSPy trial-loads a copy first, so a bad artifact leaves `program` unchanged.
        if target:
            _atomic_write(Path(target), content)
        if self.path and os.path.lexists(self.path):
            stat = self._regular_stat(self.path)
            self._stat = (stat.st_mtime_ns, stat.st_size)
        self.digest = hashlib.sha256(content).hexdigest()
