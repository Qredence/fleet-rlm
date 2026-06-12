"""GEPA candidate evidence serialization helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def extract_candidate_prompts(candidate: Any) -> dict[str, str]:
    """Extract predictor instructions from a GEPA candidate/module."""
    if isinstance(candidate, dict):
        return {str(key): str(value) for key, value in candidate.items()}
    prompts: dict[str, str] = {}
    named_predictors = getattr(candidate, "named_predictors", None)
    if not callable(named_predictors):
        return prompts
    try:
        for name, predictor in named_predictors():
            instructions = getattr(getattr(predictor, "signature", None), "instructions", None)
            if instructions is not None:
                prompts[str(name)] = str(instructions)
    except Exception:
        return {}
    return prompts


def json_safe(value: Any, *, depth: int = 0) -> Any:
    """Return a compact JSON-safe representation of GEPA library objects."""
    if depth > 4:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return [json_safe(item, depth=depth + 1) for item in sorted(value, key=str)]
    if isinstance(value, (list, tuple)):
        return [json_safe(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item, depth=depth + 1) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        try:
            return json_safe(value.model_dump(), depth=depth + 1)
        except Exception:
            pass
    if hasattr(value, "to_dict"):
        try:
            return json_safe(value.to_dict(), depth=depth + 1)
        except Exception:
            pass
    prompts = extract_candidate_prompts(value)
    if prompts:
        return prompts
    return str(value)


def best_candidate_index(scores: list[float], explicit_best_idx: Any = None) -> int | None:
    """Resolve GEPA's selected candidate index from explicit metadata or scores."""
    if explicit_best_idx is not None:
        try:
            return int(explicit_best_idx)
        except (TypeError, ValueError):
            pass
    if not scores:
        return None
    return max(range(len(scores)), key=lambda index: scores[index])


def build_gepa_candidate_evidence(
    *,
    detailed_results: Any,
    evidence_path: Path,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Normalize DSPy GEPA detailed results into Fleet review evidence."""
    if detailed_results is None:
        return None, []

    raw_candidates = list(getattr(detailed_results, "candidates", []) or [])
    raw_scores = list(getattr(detailed_results, "val_aggregate_scores", []) or [])
    scores: list[float] = []
    for score in raw_scores:
        try:
            scores.append(float(score))
        except (TypeError, ValueError):
            scores.append(0.0)

    try:
        explicit_best_idx = getattr(detailed_results, "best_idx", None)
    except Exception:
        explicit_best_idx = None
    best_idx = best_candidate_index(scores, explicit_best_idx)
    seed_score = scores[0] if scores else None
    parents = list(getattr(detailed_results, "parents", []) or [])
    discovery_eval_counts = list(getattr(detailed_results, "discovery_eval_counts", []) or [])

    candidates: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    total = max(len(raw_candidates), len(scores))
    for index in range(total):
        candidate = raw_candidates[index] if index < len(raw_candidates) else {}
        score = scores[index] if index < len(scores) else None
        status = "selected" if best_idx == index else "rejected"
        score_delta = round(score - seed_score, 4) if score is not None and seed_score is not None else None
        prompts = extract_candidate_prompts(candidate)
        candidates.append(
            {
                "candidate_id": f"candidate-{index}",
                "status": status,
                "score": score,
                "score_delta_from_seed": score_delta,
                "parents": parents[index] if index < len(parents) else [],
                "discovery_eval_count": discovery_eval_counts[index] if index < len(discovery_eval_counts) else None,
                "prompts": prompts,
            }
        )
        decisions.append(
            {
                "candidate_id": f"candidate-{index}",
                "status": status,
                "summary": (
                    "GEPA selected this prompt candidate as the best artifact."
                    if status == "selected"
                    else "GEPA rejected this prompt candidate in favor of a stronger candidate."
                ),
                "rationale": (
                    f"Aggregate GEPA validation score={score}."
                    if score is not None
                    else "GEPA did not expose an aggregate score for this candidate."
                ),
                "score": score,
                "score_delta": score_delta,
                "artifact_path": str(evidence_path),
                "missing_candidate_artifact": False,
            }
        )

    raw = json_safe(detailed_results)
    evidence = {
        "version": 1,
        "source": "dspy.GEPA.detailed_results",
        "best_candidate_id": f"candidate-{best_idx}" if best_idx is not None else None,
        "best_idx": best_idx,
        "candidate_count": len(candidates),
        "total_metric_calls": json_safe(getattr(detailed_results, "total_metric_calls", None)),
        "num_full_val_evals": json_safe(getattr(detailed_results, "num_full_val_evals", None)),
        "log_dir": json_safe(getattr(detailed_results, "log_dir", None)),
        "seed": json_safe(getattr(detailed_results, "seed", None)),
        "candidates": candidates,
        "raw_summary": {
            "val_aggregate_scores": json_safe(getattr(detailed_results, "val_aggregate_scores", None)),
            "val_subscores": json_safe(getattr(detailed_results, "val_subscores", None)),
            "per_val_instance_best_candidates": json_safe(
                getattr(detailed_results, "per_val_instance_best_candidates", None)
            ),
            "best_outputs_valset": json_safe(getattr(detailed_results, "best_outputs_valset", None)),
        },
        "raw": raw,
    }
    return evidence, decisions


def write_gepa_evidence_artifact(
    *,
    optimized: Any,
    evidence_path: Path,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Persist GEPA detailed results next to the optimized artifact when present."""
    detailed_results = getattr(optimized, "detailed_results", None)
    evidence, decisions = build_gepa_candidate_evidence(
        detailed_results=detailed_results,
        evidence_path=evidence_path,
    )
    if evidence is None:
        return None, []
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return evidence, decisions


__all__ = [
    "build_gepa_candidate_evidence",
    "extract_candidate_prompts",
    "json_safe",
    "write_gepa_evidence_artifact",
]
