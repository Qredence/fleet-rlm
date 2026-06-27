"""Execution completion summary and MLflow metadata for WebSocket streaming."""

from __future__ import annotations

from typing import Any

from fleet_rlm.integrations.observability.mlflow_context import (
    merge_trace_result_metadata as _merge_trace_result_metadata,
)
from fleet_rlm.runtime.execution.final_artifact import build_final_artifact_from_answer
from fleet_rlm.runtime.execution.streaming_events import _normalize_trajectory

from ...runtime_services.chat_runtime import StreamEventLike


def merge_trace_result_metadata(
    payload: dict[str, Any] | None,
    *,
    response_preview: str | None = None,
    trace_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility shim for MLflow final-event metadata enrichment."""
    return _merge_trace_result_metadata(
        payload,
        response_preview=response_preview,
        trace_metadata=trace_metadata,
    )


def _runtime_trace_metadata(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    runtime_payload = payload.get("runtime")
    runtime = runtime_payload if isinstance(runtime_payload, dict) else {}

    metadata: dict[str, Any] = {}
    for key in (
        "routing_decision",
        "source_url",
        "execution_mode",
        "runtime_module",
    ):
        value = payload.get(key, runtime.get(key))
        if value not in (None, "", False):
            metadata[f"fleet_rlm.{key}"] = value

    selected_skills = payload.get("selected_skills")
    if isinstance(selected_skills, list):
        metadata["fleet_rlm.selected_skills"] = ",".join(str(item) for item in selected_skills if str(item))

    trajectory_steps = _normalize_trajectory(payload.get("trajectory"))
    if trajectory_steps:
        metadata["fleet_rlm.trajectory_steps"] = str(len(trajectory_steps))
        if any(step.get("thought") for step in trajectory_steps):
            metadata["fleet_rlm.trajectory_has_reasoning"] = "true"
        if any(step.get("tool_name") for step in trajectory_steps):
            metadata["fleet_rlm.trajectory_has_tools"] = "true"
        if any(
            "repl" in str(step.get("tool_name", "")).lower() or "code" in step or step.get("type") == "repl"
            for step in trajectory_steps
        ):
            metadata["fleet_rlm.trajectory_has_repl"] = "true"
        if any(step.get("output") is not None or step.get("observation") is not None for step in trajectory_steps):
            metadata["fleet_rlm.trajectory_has_outputs"] = "true"

    for key in (
        "runtime_degraded",
        "runtime_failure_category",
        "runtime_failure_phase",
        "runtime_fallback_used",
    ):
        value = payload.get(key, runtime.get(key))
        if value in (None, "", False):
            if key in {"runtime_degraded", "runtime_fallback_used"} and value is False:
                metadata[key] = False
            continue
        metadata[key] = value
    return metadata


def _as_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_text(value: Any) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    return None


def _normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_as_text(entry) for entry in value) if item is not None]


def final_event_failed(payload: dict[str, Any]) -> bool:
    runtime = _as_record(payload.get("runtime"))
    runtime_degraded = bool(payload.get("runtime_degraded", runtime.get("runtime_degraded", False)))
    category = _as_text(payload.get("runtime_failure_category") or runtime.get("runtime_failure_category"))
    return runtime_degraded and category == "tool_execution_error"


def _extract_human_review_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw = _as_record(payload.get("human_review"))
    if raw:
        required = raw.get("required")
        if required is False:
            return None
        return {
            "required": True,
            "reason": _as_text(raw.get("reason")) or "Recursive repair requested human review before continuing.",
            "repair_mode": _as_text(raw.get("repair_mode")),
            "repair_target": _as_text(raw.get("repair_target")),
            "repair_steps": _normalize_text_list(raw.get("repair_steps")),
        }

    recursive_repair = _as_record(payload.get("recursive_repair"))
    if _as_text(recursive_repair.get("repair_mode")) != "needs_human_review":
        return None

    normalized_steps = _normalize_text_list(recursive_repair.get("repair_steps"))
    return {
        "required": True,
        "reason": _as_text(payload.get("final_reasoning"))
        or _as_text(recursive_repair.get("repair_rationale"))
        or _as_text(recursive_repair.get("repair_target"))
        or "Recursive repair requested human review before continuing.",
        "repair_mode": "needs_human_review",
        "repair_target": _as_text(recursive_repair.get("repair_target")),
        "repair_steps": normalized_steps,
    }


def _canonical_run_status(
    kind: str,
    payload: dict[str, Any],
    *,
    human_review_required: bool,
) -> str:
    if kind == "done":
        # A "done" event with payload["cancelled"]=True is a cancelled turn.
        if isinstance(payload, dict) and payload.get("cancelled"):
            return "cancelled"
        if human_review_required:
            return "needs_human_review"
        return "error" if final_event_failed(payload) else "completed"
    return "error"


def _extract_run_result_answer(payload: dict[str, Any]) -> str | None:
    """Extract final_answer from run_result or the last trajectory step output."""
    run_result = _as_record(payload.get("run_result"))
    answer = _as_text(run_result.get("final_answer"))
    if answer:
        return answer

    trajectory = _normalize_trajectory(payload.get("trajectory"))
    if trajectory:
    if trajectory:
        for step in reversed(trajectory):
            val = (
                (step.get("output") or step.get("observation"))
                if isinstance(step, dict)
                else (getattr(step, "output", None) or getattr(step, "observation", None))
            )
            output = _as_text(val)
            if output:
                return output
    return None


def _build_fallback_final_artifact(
    event: StreamEventLike,
    *,
    request_message: str = "",
) -> dict[str, Any] | None:
    if event.kind != "done":
        return None
    payload = event.payload if isinstance(event.payload, dict) else {}
    routing_decision = _as_text(payload.get("routing_decision"))

    # Try run_result.final_answer or trajectory[-1].output first
    run_result_answer = _extract_run_result_answer(payload)
    if run_result_answer:
        artifact = build_final_artifact_from_answer(
            run_result_answer,
            task=request_message or None,
            routing_decision=routing_decision,
            finalization_mode="RETURN",
        )
        if artifact is not None:
            return artifact
        return {
            "kind": "assistant_response",
            "value": {
                "text": run_result_answer,
                "final_markdown": run_result_answer,
                "summary": run_result_answer,
            },
            "finalization_mode": "RETURN",
        }

    # Fall back to event.text (the DONE event's text field)
    text = _as_text(event.text)
    if not text:
        return None

    artifact = build_final_artifact_from_answer(
        text,
        task=request_message or None,
        routing_decision=routing_decision,
        finalization_mode="RETURN",
    )
    if artifact is not None:
        return artifact

    return {
        "kind": "assistant_response",
        "value": {
            "text": text,
            "final_markdown": text,
            "summary": text,
        },
        "finalization_mode": "RETURN",
    }


def _build_minimum_summary(
    *,
    event: StreamEventLike,
    summary_payload: dict[str, Any],
    warnings: list[Any],
    human_review: dict[str, Any] | None,
    termination_reason: str,
) -> dict[str, Any]:
    error_text = event.text if event.kind == "error" else None
    summary = {
        "termination_reason": termination_reason,
        "duration_ms": summary_payload.get("duration_ms"),
        "warnings": warnings,
        "error": error_text,
    }
    if human_review is not None:
        summary["human_review"] = human_review
    return summary


def _resolve_terminal_status(
    *,
    existing_status: Any,
    terminal_status: str,
) -> str:
    normalized = _as_text(existing_status)
    if terminal_status in {"needs_human_review", "error", "cancelled"}:
        return terminal_status
    return normalized or terminal_status


def _resolve_termination_reason(
    *,
    existing_reason: Any,
    event_kind: str,
    human_review_required: bool,
) -> str:
    normalized = _as_text(existing_reason)
    if human_review_required and normalized in {None, "", "done", "completed"}:
        return "needs_human_review"
    return normalized or event_kind


def build_execution_completion_summary(
    *,
    event: StreamEventLike,
    request_message: str,
    run_id: str,
) -> dict[str, Any]:
    """Build the canonical execution summary payload from a terminal event."""
    payload = _as_record(event.payload)
    runtime = _as_record(payload.get("runtime"))
    run_result = _as_record(payload.get("run_result"))
    summary_payload = _as_record(payload.get("summary"))
    payload_final_artifact = _as_record(payload.get("final_artifact"))
    human_review = _extract_human_review_payload(payload)
    runtime_mode = (
        _as_text(payload.get("runtime_mode"))
        or _as_text(runtime.get("runtime_mode"))
        or _as_text(run_result.get("runtime_mode"))
        or "daytona_pilot"
    )
    terminal_status = _canonical_run_status(
        event.kind,
        payload,
        human_review_required=human_review is not None,
    )
    resolved_termination_reason = _resolve_termination_reason(
        existing_reason=run_result.get("termination_reason") or summary_payload.get("termination_reason"),
        event_kind=event.kind,
        human_review_required=human_review is not None,
    )
    warnings = list(summary_payload.get("warnings") or payload.get("guardrail_warnings") or [])
    minimum_summary = _build_minimum_summary(
        event=event,
        summary_payload=summary_payload,
        warnings=warnings,
        human_review=human_review,
        termination_reason=resolved_termination_reason,
    )

    if run_result:
        normalized = dict(run_result)
        normalized.setdefault("run_id", run_result.get("run_id") or runtime.get("run_id") or run_id)
        normalized.setdefault("runtime_mode", runtime_mode)
        normalized.setdefault("task", run_result.get("task") or request_message)
        normalized["status"] = _resolve_terminal_status(
            existing_status=run_result.get("status"),
            terminal_status=terminal_status,
        )
        normalized["termination_reason"] = resolved_termination_reason
        normalized.setdefault("duration_ms", summary_payload.get("duration_ms"))
        normalized.setdefault("warnings", warnings)
        nested_summary = _as_record(normalized.get("summary"))
        nested_summary = {**minimum_summary, **nested_summary}
        if summary_payload:
            nested_summary = {**nested_summary, **summary_payload}
        nested_summary["termination_reason"] = resolved_termination_reason
        if warnings and not nested_summary.get("warnings"):
            nested_summary["warnings"] = warnings
        if human_review is not None:
            normalized["human_review"] = human_review
            nested_summary["human_review"] = human_review
        normalized["summary"] = nested_summary
        normalized.setdefault(
            "final_artifact",
            payload_final_artifact or _build_fallback_final_artifact(event, request_message=request_message),
        )
        return normalized

    final_artifact = payload_final_artifact or _build_fallback_final_artifact(
        event,
        request_message=request_message,
    )

    return {
        "run_id": _as_text(runtime.get("run_id")) or run_id,
        "runtime_mode": runtime_mode,
        "task": request_message,
        "status": terminal_status,
        "termination_reason": resolved_termination_reason,
        "duration_ms": summary_payload.get("duration_ms"),
        "iterations": [],
        "callbacks": [],
        "prompts": [],
        "context_sources": [],
        "sources": list(payload.get("sources") or []),
        "attachments": list(payload.get("attachments") or []),
        "final_artifact": final_artifact,
        "summary": minimum_summary,
        "warnings": warnings,
        **({"human_review": human_review} if human_review is not None else {}),
    }


def enrich_terminal_stream_payload(
    *,
    event: StreamEventLike,
    payload: dict[str, Any] | None,
    request_message: str,
    run_id: str,
) -> dict[str, Any]:
    """Attach canonical run summary + final artifact to terminal websocket payloads.

    The execution lifecycle emitter already builds this summary for the
    secondary execution stream, but the primary chat websocket previously only
    forwarded the raw runtime payload. Hydrating here keeps chat transcripts and
    the run workbench aligned when a turn completes.
    """
    merged = dict(payload or {})
    summary = build_execution_completion_summary(
        event=event,
        request_message=request_message,
        run_id=run_id,
    )
    merged.setdefault("run_summary", summary)
    merged.setdefault("final_artifact", summary.get("final_artifact"))
    merged.setdefault("status", summary.get("status"))
    nested_summary = summary.get("summary")
    if isinstance(nested_summary, dict):
        merged.setdefault("summary", nested_summary)
    if summary.get("run_id"):
        merged.setdefault("run_id", summary.get("run_id"))
    if summary.get("runtime_mode"):
        merged.setdefault("runtime_mode", summary.get("runtime_mode"))
    return merged


__all__ = [
    "merge_trace_result_metadata",
    "_runtime_trace_metadata",
    "enrich_terminal_stream_payload",
    "final_event_failed",
    "build_execution_completion_summary",
]
