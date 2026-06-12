"""Domain helpers for GEPA optimization run detail reports."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, cast

from fleet_rlm.api.schemas.optimization import (
    OptimizationArtifactRef,
    OptimizationCandidateDecision,
    OptimizationHoldoutSummary,
    OptimizationPromptDiffItem,
    OptimizationReviewBundle,
    OptimizationRunDetailResponse,
    OptimizationRunInsights,
    OptimizationRunResponse,
    OptimizationScoreSummary,
    OptimizationTraceEvidenceItem,
)

__all__ = ["build_optimization_run_detail"]

_MAX_ARTIFACT_TEXT_CHARS = 200_000


def _read_json_file(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_file():
        return None
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_text_artifact(path: str | None) -> tuple[str | None, bool]:
    if not path:
        return None, False
    candidate = Path(path)
    if not candidate.is_file():
        return None, False
    try:
        text = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, False
    if len(text) <= _MAX_ARTIFACT_TEXT_CHARS:
        return text, False
    return text[:_MAX_ARTIFACT_TEXT_CHARS], True


def _artifact_ref(label: str, path: str | None, kind: str) -> OptimizationArtifactRef | None:
    if not path:
        return None
    return OptimizationArtifactRef(label=label, path=path, kind=kind, exists=Path(path).exists())


def _artifact_refs(run: OptimizationRunResponse, review_bundle: dict[str, Any] | None) -> list[OptimizationArtifactRef]:
    refs = [
        _artifact_ref("Optimized artifact", run.output_path, "output"),
        _artifact_ref("Manifest", run.manifest_path, "manifest"),
        _artifact_ref("Distilled trace bundle", run.distilled_trace_bundle_path, "trace_bundle"),
        _artifact_ref("Raw trace export", run.raw_trace_export_path, "raw_trace_export"),
    ]
    artifact = review_bundle.get("artifact") if isinstance(review_bundle, dict) else None
    if isinstance(artifact, dict):
        refs.append(_artifact_ref("Review artifact", _optional_str(artifact.get("path")), "review_artifact"))
    gepa_evidence = review_bundle.get("gepa_evidence") if isinstance(review_bundle, dict) else None
    if isinstance(gepa_evidence, dict):
        refs.append(_artifact_ref("GEPA candidate evidence", _optional_str(gepa_evidence.get("path")), "gepa_evidence"))
        refs.append(_artifact_ref("GEPA log directory", _optional_str(gepa_evidence.get("log_dir")), "gepa_log_dir"))
    seen: set[tuple[str, str]] = set()
    result: list[OptimizationArtifactRef] = []
    for ref in refs:
        if ref is None:
            continue
        key = (ref.kind, ref.path)
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def _optional_str(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _optional_float(value: object) -> float | None:
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    if not isinstance(value, (int, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _list_str(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in (None, "")]


def _review_bundle(manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(manifest, dict):
        return None
    value = manifest.get("review_bundle")
    return value if isinstance(value, dict) else None


def _build_score_summary(
    run: OptimizationRunResponse,
    manifest: dict[str, Any] | None,
    review_bundle: dict[str, Any] | None,
) -> OptimizationScoreSummary:
    holdout = review_bundle.get("holdout") if isinstance(review_bundle, dict) else None
    holdout_dict = holdout if isinstance(holdout, dict) else {}
    split = holdout_dict.get("split_reference")
    split_dict = split if isinstance(split, dict) else {}
    return OptimizationScoreSummary(
        baseline_score=_optional_float(holdout_dict.get("baseline_score")),
        optimized_score=_optional_float(holdout_dict.get("optimized_score") or run.validation_score),
        score_delta=_optional_float(holdout_dict.get("score_delta")),
        train_examples=_optional_int(split_dict.get("train_examples") or run.train_examples),
        validation_examples=_optional_int(split_dict.get("validation_examples") or run.validation_examples),
        train_ratio=_optional_float(split_dict.get("train_ratio") or run.train_ratio),
        split_strategy=_optional_str(split_dict.get("strategy") or (manifest or {}).get("split_strategy")),
    )


def _semantic_prompt_changed(before_prompt: str, after_prompt: str) -> bool:
    return before_prompt.strip() != after_prompt.strip()


def _prompt_diffs_from_manifest(review_bundle: dict[str, Any] | None) -> list[OptimizationPromptDiffItem]:
    snapshots = review_bundle.get("prompt_snapshots") if isinstance(review_bundle, dict) else None
    snapshot_dict = snapshots if isinstance(snapshots, dict) else {}
    matched = snapshot_dict.get("matched_predictors")
    if not isinstance(matched, list):
        return []
    diffs: list[OptimizationPromptDiffItem] = []
    for index, item in enumerate(matched):
        if not isinstance(item, dict):
            continue
        item_dict = cast(dict[str, Any], item)
        before_prompt = str(item_dict.get("before_prompt") or "")
        after_prompt = str(item_dict.get("after_prompt") or "")
        diffs.append(
            OptimizationPromptDiffItem(
                predictor_name=str(item_dict.get("predictor_name") or f"prompt-{index + 1}"),
                before_prompt=before_prompt,
                after_prompt=after_prompt,
                changed=_semantic_prompt_changed(before_prompt, after_prompt),
            )
        )
    return diffs


def _prompt_diffs_from_snapshots(snapshots: list[Any]) -> list[OptimizationPromptDiffItem]:
    by_name: dict[str, dict[str, str]] = {}
    for snapshot in snapshots:
        predictor_name = str(getattr(snapshot, "predictor_name", "") or "prompt")
        prompt_type = getattr(snapshot, "prompt_type", "")
        prompt_type_value = prompt_type.value if hasattr(prompt_type, "value") else str(prompt_type)
        by_name.setdefault(predictor_name, {})[prompt_type_value] = str(getattr(snapshot, "prompt_text", "") or "")
    diffs: list[OptimizationPromptDiffItem] = []
    for predictor_name, prompts in sorted(by_name.items()):
        before_prompt = prompts.get("before", "")
        after_prompt = prompts.get("after", "")
        if before_prompt or after_prompt:
            diffs.append(
                OptimizationPromptDiffItem(
                    predictor_name=predictor_name,
                    before_prompt=before_prompt,
                    after_prompt=after_prompt,
                    changed=_semantic_prompt_changed(before_prompt, after_prompt),
                )
            )
    return diffs


def _trace_bundle_paths(run: OptimizationRunResponse, review_bundle: dict[str, Any] | None) -> list[str]:
    paths = []
    if isinstance(review_bundle, dict):
        paths.extend(_list_str(review_bundle.get("trace_bundle_paths")))
    if run.distilled_trace_bundle_path:
        paths.append(run.distilled_trace_bundle_path)
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result


def _read_distilled_trace_evidence(paths: list[str]) -> list[OptimizationTraceEvidenceItem]:
    evidence: list[OptimizationTraceEvidenceItem] = []
    for path in paths:
        candidate = Path(path)
        if not candidate.is_file():
            continue
        try:
            lines = candidate.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "")
            if kind not in {"trace_bundle_summary", "trace_evidence"}:
                continue
            clusters = item.get("failure_clusters")
            cluster_categories = []
            if isinstance(clusters, list):
                cluster_categories = [
                    str(cluster.get("category"))
                    for cluster in clusters
                    if isinstance(cluster, dict) and cluster.get("category")
                ]
            evidence.append(
                OptimizationTraceEvidenceItem(
                    kind=kind,
                    trace_id=_optional_str(item.get("trace_id")),
                    session_id=_optional_str(item.get("session_id")),
                    client_request_id=_optional_str(item.get("client_request_id")),
                    trace_count=_optional_int(item.get("trace_count")),
                    span_count=_optional_int(item.get("span_count")),
                    failure_categories=_list_str(item.get("failure_categories")) or cluster_categories,
                    prompt_change_recommendations=_list_str(item.get("prompt_change_recommendations")),
                )
            )
    return evidence


def _recommendations_from_trace_evidence(evidence: list[OptimizationTraceEvidenceItem]) -> list[str]:
    seen: set[str] = set()
    recommendations: list[str] = []
    for item in evidence:
        for recommendation in item.prompt_change_recommendations:
            if recommendation in seen:
                continue
            seen.add(recommendation)
            recommendations.append(recommendation)
    return recommendations


def _has_external_validation(review_bundle: dict[str, Any] | None) -> bool:
    """Return whether the run contains a true holdout validation split."""
    holdout = review_bundle.get("holdout") if isinstance(review_bundle, dict) else None
    if not isinstance(holdout, dict):
        return True
    value = holdout.get("external_validation_available")
    return bool(value) if isinstance(value, bool) else True


def _selected_outcome(run: OptimizationRunResponse, prompt_diffs: list[OptimizationPromptDiffItem]) -> str:
    if run.status == "failed":
        return "failed"
    if run.status in {"running", "pending"}:
        return "running"
    if prompt_diffs:
        return "changed" if any(diff.changed for diff in prompt_diffs) else "unchanged"
    return "unknown"


def _candidate_decisions(
    *,
    run: OptimizationRunResponse,
    manifest: dict[str, Any] | None,
    prompt_diffs: list[OptimizationPromptDiffItem],
    score_summary: OptimizationScoreSummary,
) -> list[OptimizationCandidateDecision]:
    insights = manifest.get("insights") if isinstance(manifest, dict) else None
    if not isinstance(insights, dict):
        review_bundle = _review_bundle(manifest)
        insights = review_bundle.get("insights") if isinstance(review_bundle, dict) else None
    decisions = insights.get("candidate_decisions") if isinstance(insights, dict) else None
    if isinstance(decisions, list) and decisions:
        return [
            OptimizationCandidateDecision(
                candidate_id=str(item_dict.get("candidate_id") or f"candidate-{index + 1}"),
                status=str(item_dict.get("status") or "unknown"),
                summary=str(item_dict.get("summary") or ""),
                rationale=_optional_str(item_dict.get("rationale")),
                score=_optional_float(item_dict.get("score")),
                score_delta=_optional_float(item_dict.get("score_delta")),
                artifact_path=_optional_str(item_dict.get("artifact_path")),
                missing_candidate_artifact=_optional_bool(item_dict.get("missing_candidate_artifact")),
            )
            for index, item in enumerate(decisions)
            if isinstance(item, dict)
            for item_dict in [cast(dict[str, Any], item)]
        ]

    outcome = _selected_outcome(run, prompt_diffs)
    if outcome == "changed":
        return [
            OptimizationCandidateDecision(
                candidate_id="selected",
                status="selected",
                summary="GEPA selected a prompt change for the optimized artifact.",
                rationale="The selected candidate is represented by the after prompt snapshot.",
                score=score_summary.optimized_score,
                score_delta=score_summary.score_delta,
                artifact_path=run.output_path,
            )
        ]
    if outcome == "unchanged":
        return [
            OptimizationCandidateDecision(
                candidate_id="selected",
                status="selected",
                summary="GEPA kept the original prompt as the best selected artifact.",
                rationale="The final before/after prompt snapshots are semantically unchanged.",
                score=score_summary.optimized_score,
                score_delta=score_summary.score_delta,
                artifact_path=run.output_path,
            ),
            OptimizationCandidateDecision(
                candidate_id="rejected-candidates",
                status="unavailable",
                summary="Rejected proposal artifacts were not persisted for this run.",
                rationale=(
                    "GEPA may have explored candidate prompts, but this manifest does not contain "
                    "candidate-level artifact snapshots or rejection scores."
                ),
                missing_candidate_artifact=True,
            ),
        ]
    if outcome == "failed":
        return [
            OptimizationCandidateDecision(
                candidate_id="failed",
                status="failed",
                summary="GEPA did not produce a selected candidate because the run failed.",
                rationale=run.error,
            )
        ]
    return [
        OptimizationCandidateDecision(
            candidate_id="candidate-data",
            status="unavailable",
            summary="Candidate decision data is not available for this run yet.",
            missing_candidate_artifact=True,
        )
    ]


def _deserialize_insights(raw: object) -> OptimizationRunInsights | None:
    if not isinstance(raw, dict):
        return None
    try:
        return OptimizationRunInsights.model_validate(raw)
    except ValueError:
        return None


def _typed_review_bundle(review_bundle: dict[str, Any] | None) -> OptimizationReviewBundle | None:
    if not isinstance(review_bundle, dict):
        return None
    holdout_raw = review_bundle.get("holdout")
    holdout = None
    if isinstance(holdout_raw, dict):
        holdout = OptimizationHoldoutSummary(
            promotion_ready=bool(holdout_raw.get("promotion_ready", False)),
            external_validation_available=bool(holdout_raw.get("external_validation_available", True)),
            baseline_score=_optional_float(holdout_raw.get("baseline_score")),
            optimized_score=_optional_float(holdout_raw.get("optimized_score")),
            score_delta=_optional_float(holdout_raw.get("score_delta")),
        )
    insights = _deserialize_insights(review_bundle.get("insights"))
    if holdout is None and insights is None:
        return None
    version_raw = review_bundle.get("version")
    version = int(version_raw) if isinstance(version_raw, int) else 1
    return OptimizationReviewBundle(version=version, holdout=holdout, insights=insights)


def _insights_from_manifest(
    *,
    manifest: dict[str, Any] | None,
    review_bundle: dict[str, Any] | None,
) -> OptimizationRunInsights | None:
    for source in (review_bundle, manifest):
        if not isinstance(source, dict):
            continue
        insights = _deserialize_insights(source.get("insights"))
        if insights is not None:
            return insights
    return None


def _build_insights(
    *,
    run: OptimizationRunResponse,
    prompt_diffs: list[OptimizationPromptDiffItem],
    recommendations: list[str],
    has_external_validation: bool = True,
) -> OptimizationRunInsights:
    outcome = _selected_outcome(run, prompt_diffs)
    if outcome == "changed":
        summary = "GEPA selected an updated prompt artifact to improve the executor RLM target."
        next_step = "Review the prompt diff and create a promotion draft before applying the artifact manually."
    elif outcome == "unchanged":
        summary = "GEPA evaluated the executor RLM prompt and kept the original prompt as the selected artifact."
        next_step = "Add more validation examples or run with a larger GEPA budget before promoting changes."
    elif outcome == "failed":
        summary = "GEPA did not complete, so no prompt improvement was selected."
        next_step = "Inspect the run error and trace evidence, then rerun with corrected inputs."
    elif outcome == "running":
        summary = "GEPA is still running; selected prompt details are not final yet."
        next_step = "Wait for completion, then review the prompt diff and trace evidence."
    else:
        summary = "GEPA run details are partially available, but prompt outcome could not be determined."
        next_step = "Inspect the manifest and ensure prompt snapshots are persisted for future runs."
    if run.status == "completed" and not has_external_validation:
        next_step = "Add holdout validation examples before treating this draft as promotion-ready."
    normalized_outcome = cast(
        Literal["changed", "unchanged", "failed", "running", "unknown"],
        outcome if outcome in {"changed", "unchanged", "failed", "running", "unknown"} else "unknown",
    )
    return OptimizationRunInsights(
        selected_outcome=normalized_outcome,
        summary=summary,
        trace_driven_recommendations=recommendations,
        next_step=next_step,
    )


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "run"


def build_optimization_run_detail(
    *,
    run: OptimizationRunResponse,
    prompt_snapshots: list[Any],
) -> OptimizationRunDetailResponse:
    """Build a detailed, trace-safe GEPA improvement report for one run."""
    manifest = _read_json_file(run.manifest_path)
    review_bundle = _review_bundle(manifest)
    prompt_diffs = _prompt_diffs_from_manifest(review_bundle) or _prompt_diffs_from_snapshots(prompt_snapshots)
    trace_evidence = _read_distilled_trace_evidence(_trace_bundle_paths(run, review_bundle))
    score_summary = _build_score_summary(run, manifest, review_bundle)
    recommendations = _recommendations_from_trace_evidence(trace_evidence)
    artifact_text, artifact_truncated = _read_text_artifact(run.output_path)
    candidate_decisions = _candidate_decisions(
        run=run,
        manifest=manifest,
        prompt_diffs=prompt_diffs,
        score_summary=score_summary,
    )
    stored_insights = _insights_from_manifest(manifest=manifest, review_bundle=review_bundle)
    return OptimizationRunDetailResponse(
        run=run,
        manifest_available=manifest is not None,
        manifest=manifest,
        review_bundle=review_bundle,
        typed_review_bundle=_typed_review_bundle(review_bundle),
        artifact_refs=_artifact_refs(run, review_bundle),
        score_summary=score_summary,
        prompt_diffs=prompt_diffs,
        trace_evidence=trace_evidence,
        candidate_decisions=candidate_decisions,
        insights=stored_insights
        or _build_insights(
            run=run,
            prompt_diffs=prompt_diffs,
            recommendations=recommendations,
            has_external_validation=_has_external_validation(review_bundle),
        ),
        optimized_artifact_text=artifact_text,
        optimized_artifact_truncated=artifact_truncated,
    )
