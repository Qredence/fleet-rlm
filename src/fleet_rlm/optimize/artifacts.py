"""Candidate identity, component comparisons, manifests, reports, promotion gates, and approval."""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import os
import platform
import re
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from importlib.metadata import distributions
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, cast

import dspy
import mlflow

from fleet_rlm.config import FleetSettings

_FLEX_TYPE = cast(type[dspy.Module], dspy.Flex)

APPROVAL_POLICY_VERSION = 6
APPROVAL_EVIDENCE_VERSION = 2
VALIDATION_PASSES = 3


# Execution, review selection, and scoring affect approval; presentation-only code does not.
DIGEST_SOURCES = (
    "adapters.py",
    "config.py",
    "contracts.py",
    "execution_evidence.py",
    "optimization_runs.py",
    "observability.py",
    "replay.py",
    "workspace.py",
    "skills.py",
    "session.py",
    "store.py",
)


def optimization_code_digest() -> str:
    """Hash the runtime/evaluation source that determines candidate behavior and scoring."""
    package = Path(__file__).parents[1]
    paths = {package / name for name in DIGEST_SOURCES}
    paths |= set((package / "engine").rglob("*.py"))
    paths |= set((package / "optimize").rglob("*.py"))
    paths |= set((package / "sandbox").rglob("*.py"))
    paths |= set((package / "sandbox").rglob("snapshot-requirements.txt"))
    paths |= {path for path in (package / "bundled_skills").rglob("*") if path.is_file()}
    material = [
        (path.relative_to(package).as_posix(), digest_bytes(path.read_bytes()))
        for path in sorted(paths)
        if path.is_file()
    ]
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def installed_dependency_fingerprint() -> str:
    """Hash the installed environment, independent of source-tree or wheel layout."""
    installed = sorted(
        (
            re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower(),
            distribution.version,
        )
        for distribution in distributions()
        if distribution.metadata["Name"]
    )
    if not installed:
        raise RuntimeError("No installed Python distributions are available for approval evidence")
    return hashlib.sha256(json.dumps(installed, separators=(",", ":")).encode()).hexdigest()


def approval_evidence(settings: FleetSettings, incumbent_digest: str, *, scorer_version: str) -> dict[str, str]:
    """Evidence must still match when a separately invoked approval installs the candidate."""
    return {
        "evidence_format": str(APPROVAL_EVIDENCE_VERSION),
        "dspy": dspy.__version__,
        "gepa": package_version("gepa"),
        "mlflow": mlflow.__version__,
        "fleet_rlm": package_version("fleet-rlm"),
        "python": platform.python_version(),
        "scorer": scorer_version,
        "configuration_digest": settings.configuration()["digest"],
        "code_digest": optimization_code_digest(),
        "dependency_fingerprint": installed_dependency_fingerprint(),
        "incumbent_digest": incumbent_digest,
    }


def format_report(manifest: ArtifactManifest, holdout: list[dspy.Example]) -> str:
    """Scores per stage and per holdout example, so whoever approves can tell a gain from noise."""
    baseline, candidate = manifest.evaluation["baseline"], manifest.evaluation["candidate"]
    lines = [
        f"## Fleet-RLM Lite GEPA candidate `{manifest.version}`",
        "",
        "| Split | Baseline | Candidate | Delta |",
        "|---|---:|---:|---:|",
    ]
    for split in ("dev", "holdout"):
        lines.append(
            f"| {split} | {baseline[split]:.3f} | {candidate[split]:.3f} | {candidate[split] - baseline[split]:+.3f} |"
        )
    lines += ["", "| Holdout example | Baseline | Candidate |", "|---|---:|---:|"]
    for example, before, after in zip(holdout, baseline["holdout_scores"], candidate["holdout_scores"], strict=True):
        lines.append(f"| {' '.join(str(example.task).split())[:60]} | {before:.3f} | {after:.3f} |")
    verdict = "PASS" if manifest.gate["passed"] else "FAIL: " + "; ".join(manifest.gate["reasons"])
    changed = ", ".join(candidate.get("changed_components") or []) or "none"
    objectives = ", ".join(
        f"{name} {baseline['holdout_objectives'].get(name, 0):.2f}->{value:.2f}"
        for name, value in candidate.get("holdout_objectives", {}).items()
    )
    critical_scores = candidate.get("holdout_critical_scores", [])
    critical_summary = (
        f"{sum(score == 1.0 for score in critical_scores)}/{len(critical_scores)} passed"
        if critical_scores
        else "none labeled"
    )
    lines += [
        "",
        f"Critical deterministic holdout cases: {critical_summary}",
        f"Holdout rollout errors: {candidate.get('holdout_errors', 'unavailable')}",
        f"Holdout objectives: {objectives or 'none'}",
        f"Changed components: {changed}",
        f"Gate: {verdict}",
    ]
    for split in ("dev", "holdout"):
        for side in ("baseline", "candidate"):
            diagnostics = manifest.evaluation[side]
            calls = diagnostics.get(f"{split}_lm_calls", [])
            calls_median = diagnostics.get(f"{split}_lm_calls_median")
            calls_measured = diagnostics.get(f"{split}_lm_calls_measured", 0)
            runtime_values = diagnostics.get(f"{split}_seconds_per_example", [])
            runtime_median = diagnostics.get(f"{split}_seconds_per_example_median")
            artifact_passes = diagnostics.get(f"{split}_artifact_passes", 0)
            artifact_cases = diagnostics.get(f"{split}_artifact_cases", 0)
            calls_text = f"{calls_median:g}" if isinstance(calls_median, (int, float)) else "unavailable"
            runtime_text = f"{runtime_median:g}s" if isinstance(runtime_median, (int, float)) else "unavailable"
            lines.append(
                f"{split.title()} {side} diagnostics: LM calls median {calls_text} "
                f"({calls_measured}/{len(calls)} measured); runtime median {runtime_text} "
                f"({len(runtime_values)} examples); artifact passes {artifact_passes}/{artifact_cases} applicable"
            )
    failures = [(split, failure) for split in ("dev", "holdout") for failure in candidate.get(f"{split}_failures", [])]
    if failures:
        lines += ["", "## Validation failures", "| Split | Case | Error | Detail |", "|---|---|---|---|"]
        for split, failure in failures:
            detail = " ".join(str(failure.get("detail", "")).split())[:500].replace("|", "\\|")
            lines.append(
                f"| {split} | `{failure.get('case_id', 'unknown')}` | {failure.get('type', 'Error')} | {detail} |"
            )
    component_changes = candidate.get("component_diffs") or []
    if component_changes:
        lines += ["", "## Candidate diffs"]
        for item in component_changes:
            lines += [f"### `{item['component']}`", "```diff", item["diff"].rstrip(), "```"]
    elif not candidate.get("changed_components"):
        lines += ["", "No predictor instructions or Flex source changed."]
    else:
        lines += ["", "Component diff unavailable for this saved candidate."]
    return "\n".join(lines)


def digest_bytes(data: bytes) -> str:
    """Return the SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def tunable_components(program: dspy.Module) -> dict[str, str]:
    """Inspect native GEPA instruction and Flex code components through public DSPy traversal."""
    components = {name: predictor.signature.instructions for name, predictor in program.named_predictors()}
    for name, parameter in program.named_parameters():
        if isinstance(parameter, _FLEX_TYPE):
            components[name] = parameter.module_src or ""
    return components


_COMPONENT_DIFF_MAX_CHARS = 8000


def component_diffs(baseline: dspy.Module, candidate: dspy.Module) -> list[dict[str, str]]:
    """Return exact before/after component text and a bounded unified diff for review."""
    before, after = tunable_components(baseline), tunable_components(candidate)
    changes = []
    for name in sorted(after):
        old, new = before.get(name, ""), after[name]
        if name in before and old == new:
            continue
        diff = "".join(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=f"incumbent/{name}",
                tofile=f"candidate/{name}",
            )
        )
        if len(diff) > _COMPONENT_DIFF_MAX_CHARS:
            diff = diff[:_COMPONENT_DIFF_MAX_CHARS] + "\n… diff truncated; full text is retained below."
        changes.append({"component": name, "before": old, "after": new, "diff": diff})
    return changes


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
        stage_evidence = evaluation.get(stage)
        if not isinstance(stage_evidence, Mapping):
            return EvaluationGate(False, (f"{stage} validation evidence is invalid",))
        passes = stage_evidence.get("passes")
        if not isinstance(passes, list) or len(passes) != VALIDATION_PASSES:
            return EvaluationGate(False, ("three paired validation passes are required",))
        observations[stage] = passes
        for item in passes:
            if not isinstance(item, Mapping):
                return EvaluationGate(False, (f"{stage} validation pass is invalid",))
            for split in ("dev", "holdout"):
                score = item.get(split)
                objectives = item.get(f"{split}_objectives")
                if not isinstance(objectives, Mapping):
                    return EvaluationGate(False, ("missing score or correctness evidence",))
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
    if manifest.dependencies.get("evidence_format") != str(APPROVAL_EVIDENCE_VERSION):
        raise ValueError("candidate evidence format is obsolete; reevaluate before approval")
    if evidence.get("evidence_format") != str(APPROVAL_EVIDENCE_VERSION):
        raise ValueError("current approval evidence format is unsupported")
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
