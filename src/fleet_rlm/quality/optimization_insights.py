"""Canonical GEPA manifest insight synthesis."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def trace_recommendations(trace_bundle_paths: list[str] | None) -> list[str]:
    """Read prompt-change recommendations from distilled trace bundles."""
    recommendations: list[str] = []
    seen: set[str] = set()
    for raw_path in trace_bundle_paths or []:
        path = Path(raw_path)
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            values = item.get("prompt_change_recommendations")
            if not isinstance(values, list):
                continue
            for value in values:
                recommendation = str(value or "").strip()
                if not recommendation or recommendation in seen:
                    continue
                seen.add(recommendation)
                recommendations.append(recommendation)
    return recommendations


def build_manifest_insights(
    *,
    prompt_snapshot_pairs: list[dict[str, Any]],
    trace_bundle_paths: list[str] | None,
    validation_score: float | None,
    baseline_validation_score: float | None,
    candidate_decisions: list[dict[str, Any]] | None = None,
    has_external_validation: bool = True,
) -> dict[str, Any]:
    """Build a normalized GEPA improvement summary for manifests."""
    prompt_changed = any(
        str(pair.get("before_prompt") or "").strip() != str(pair.get("after_prompt") or "").strip()
        for pair in prompt_snapshot_pairs
    )
    selected_outcome = "changed" if prompt_changed else "unchanged"
    score_delta = (
        round(validation_score - baseline_validation_score, 4)
        if validation_score is not None and baseline_validation_score is not None
        else None
    )
    if selected_outcome == "changed":
        summary = "GEPA selected an updated prompt artifact to improve the executor RLM target."
        next_step = "Review the prompt diff and create a promotion draft before manual application."
        candidate_summary = "GEPA selected a prompt change for the optimized artifact."
        candidate_rationale = "The selected candidate is represented by the after prompt snapshot."
    else:
        summary = "GEPA evaluated the executor RLM prompt and kept the original prompt as the selected artifact."
        next_step = "Add validation examples or increase the GEPA budget before promoting changes."
        candidate_summary = "GEPA kept the original prompt as the best selected artifact."
        candidate_rationale = "The final before/after prompt snapshots are semantically unchanged."
    if not has_external_validation:
        next_step = "Add holdout validation examples before treating this draft as promotion-ready."

    decisions = candidate_decisions or [
        {
            "candidate_id": "selected",
            "status": "selected",
            "summary": candidate_summary,
            "rationale": candidate_rationale,
            "score": validation_score,
            "score_delta": score_delta,
        },
        {
            "candidate_id": "rejected-candidates",
            "status": "unavailable",
            "summary": "Rejected proposal artifacts were not persisted for this run.",
            "rationale": (
                "GEPA candidate-level artifacts are not available in the manifest; "
                "trace recommendations still explain what the proposer tried to improve."
            ),
            "missing_candidate_artifact": True,
        },
    ]

    return {
        "selected_outcome": selected_outcome,
        "summary": summary,
        "trace_driven_recommendations": trace_recommendations(trace_bundle_paths),
        "candidate_decisions": decisions,
        "score_rationale": {
            "baseline_score": baseline_validation_score,
            "optimized_score": validation_score,
            "score_delta": score_delta,
            "external_validation_available": has_external_validation,
        },
        "next_step": next_step,
    }


__all__ = ["build_manifest_insights", "trace_recommendations"]
