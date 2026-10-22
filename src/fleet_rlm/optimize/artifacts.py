"""Compiled-program artifacts: isolated rollouts, manifests, the promotion gate, approval, and the loader."""

from __future__ import annotations

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

APPROVAL_POLICY_VERSION = 5
VALIDATION_PASSES = 3


class IsolatedFleetProgram(dspy.Module):
    """Expose candidate predictors while giving every rollout a private runtime."""

    def __init__(self, program: dspy.Module, engine_factory: Any):
        """Wrap the program so each forward call builds its own engine."""
        super().__init__()
        self.program = program
        self.engine_factory = engine_factory

    def forward(self, **inputs: Any) -> dspy.Prediction:
        """Run one rollout in a fresh engine and always shut that engine's sandbox down.

        The rollout's optimization artifact file is removed even when the call raises.
        """
        engine = self.engine_factory(dspy.Example(**inputs), self.program)
        try:
            arguments = {
                name: value
                for name, value in inputs.items()
                if name in {"workspace_manifest", "skills_catalog", "memory_summary", "task", "session_history"}
            }
            if hasattr(engine, "optimization_context"):
                arguments["context"] = engine.optimization_context
            return engine(**arguments)
        finally:
            try:
                engine.sandbox_mgr.shutdown()
            finally:
                artifact_path = getattr(engine, "optimization_artifact_path", None)
                if artifact_path:
                    Path(artifact_path).unlink(missing_ok=True)


def digest_bytes(data: bytes) -> str:
    """Return the SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class EvaluationGate:
    """Hold a promotion verdict and the reasons it failed, if any."""

    passed: bool
    reasons: tuple[str, ...]


def promotion_gate(evaluation: Mapping[str, Any]) -> EvaluationGate:
    """Recompute the policy from the three paired observations, never trust a saved verdict.

    Return an EvaluationGate with rejection reasons for an obsolete policy version,
    any non-None objective policy, or failed evidence and improvement checks.
    """
    if evaluation.get("policy_version") != APPROVAL_POLICY_VERSION:
        return EvaluationGate(False, ("obsolete approval policy; reevaluate against the current incumbent",))
    # Retired dataset-specific objectives are not comparable with Fleet's metric.
    if evaluation.get("objective_policy") is not None:
        return EvaluationGate(False, ("unsupported objective policy; reevaluate with Fleet metrics",))
    reasons: list[str] = []
    observations = {}
    for stage in ("baseline", "candidate"):
        passes = (evaluation.get(stage) or {}).get("passes")
        if not isinstance(passes, list) or len(passes) != VALIDATION_PASSES:
            return EvaluationGate(False, ("three paired validation passes are required",))
        observations[stage] = passes
        for item in passes:
            for split in ("dev", "holdout"):
                score = item.get(split)
                objectives = item.get(f"{split}_objectives") or {}
                quality = objectives.get("quality")
                if any(
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(value)
                    or not 0 <= value <= 1
                    for value in (score, quality)
                ):
                    return EvaluationGate(False, ("missing score or correctness evidence",))
                if objectives.get("usage_available") != 1.0:
                    reasons.append(f"{stage} {split} execution usage unavailable")
            if stage == "candidate":
                errors = item.get("holdout_errors")
                if type(errors) is not int or errors != 0:
                    reasons.append(f"candidate holdout rollouts errored ({errors})")
                critical = item.get("holdout_critical_scores")
                if not isinstance(critical, list) or not critical:
                    reasons.append("missing critical holdout scores")
                elif any(type(value) not in (int, float) or value != 1.0 for value in critical):
                    reasons.append("critical deterministic holdout cases failed")
    for split in ("dev", "holdout"):
        before = sum(item[split] for item in observations["baseline"]) / VALIDATION_PASSES
        after = sum(item[split] for item in observations["candidate"]) / VALIDATION_PASSES
        if (split == "dev" and after <= before) or (split == "holdout" and after < before):
            reasons.append("dev did not improve" if split == "dev" else "holdout regressed")
        quality_before = (
            sum(item[f"{split}_objectives"]["quality"] for item in observations["baseline"]) / VALIDATION_PASSES
        )
        quality_after = (
            sum(item[f"{split}_objectives"]["quality"] for item in observations["candidate"]) / VALIDATION_PASSES
        )
        if quality_after < quality_before:
            reasons.append(f"{split} correctness regressed")
    if not (evaluation.get("candidate") or {}).get("changed_components"):
        reasons.append("candidate changes no instructions or Flex code")
    return EvaluationGate(not reasons, tuple(dict.fromkeys(reasons)))


@dataclass(frozen=True)
class ArtifactManifest:
    """Describe one compiled-program digest and the evidence that approved it."""

    version: str
    digest: str
    created_at: str
    dataset_lineage: Mapping[str, Any]
    evaluation: Mapping[str, Any]
    gate: Mapping[str, Any]
    dependencies: Mapping[str, str] = field(default_factory=dict)


def write_manifest(path: Path, manifest: ArtifactManifest) -> None:
    """Atomically write a manifest as sorted, indented JSON."""
    atomic_write(path, json.dumps(asdict(manifest), sort_keys=True, indent=2).encode())


def approve_candidate(
    candidate_path: Path, manifest: ArtifactManifest, destination: Path, *, evidence: Mapping[str, str]
) -> None:
    """Install an evaluated candidate as the file the Session engine reloads at its next Turn.

    The gate is re-checked from the stored scores and the bytes must be the ones that were evaluated.
    Stale evidence requires reevaluation; old manifests cannot authorize a rollback.
    """
    gate = promotion_gate(manifest.evaluation)
    if not gate.passed:
        raise ValueError("candidate failed the promotion gate: " + "; ".join(gate.reasons))
    if dict(manifest.dependencies) != dict(evidence):
        raise ValueError("candidate evidence is stale; reevaluate against the current incumbent and configuration")
    incumbent = digest_bytes(destination.read_bytes()) if destination.exists() else ""
    if evidence.get("incumbent_digest") != incumbent:
        raise ValueError("active incumbent changed; reevaluate before approval")
    content = candidate_path.read_bytes()
    if digest_bytes(content) != manifest.digest:
        raise ValueError("candidate digest does not match its manifest")
    if destination.is_symlink():
        raise ValueError("Compiled program path may not be a symbolic link")
    atomic_write(destination, content)


def atomic_write(path: Path, data: bytes) -> None:
    """Replace path with data so readers never see a partial file.

    The bytes are fsynced before the rename, and a leftover temporary file is removed.
    """
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
        """Record the artifact path before any file is read or applied."""
        self.path = path
        self.digest = ""
        self._stat: tuple[int, int] | None = None

    def load_initial(self, program: dspy.Module) -> None:
        """Apply the artifact at startup when it exists; a missing file keeps the program unchanged."""
        if self.path and os.path.lexists(self.path):
            self._apply(program, self._read())

    def reload_if_changed(self, program: dspy.Module) -> None:
        """Reapply the artifact only when its mtime or size has changed since the last load."""
        if not self.path or not os.path.lexists(self.path):
            return
        stat = self._regular_stat(self.path)
        if (stat.st_mtime_ns, stat.st_size) != self._stat:
            self._apply(program, self._read())

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

    def _apply(self, program: dspy.Module, content: bytes) -> None:
        state = json.loads(content)
        if not isinstance(state, dict):
            raise ValueError("Compiled program must be a JSON object")
        if state and all(key.startswith("program.") or key == "metadata" for key in state):
            state = {key.removeprefix("program."): value for key, value in state.items()}
        # A program saved before a predictor existed keeps that predictor's current state.
        state = {**program.dump_state(), **state}
        program.load_state(state)  # DSPy trial-loads a copy first, so a bad artifact leaves `program` unchanged.
        if self.path and os.path.lexists(self.path):
            stat = self._regular_stat(self.path)
            self._stat = (stat.st_mtime_ns, stat.st_size)
        self.digest = hashlib.sha256(content).hexdigest()
