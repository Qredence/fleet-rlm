"""Pure helpers for terminal WebSocket execution summary shaping."""

from __future__ import annotations

from typing import Any

from .types import StreamEventLike


def _as_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_text(value: Any) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    return None


def final_event_failed(payload: dict[str, Any]) -> bool:
    runtime = _as_record(payload.get("runtime"))
    runtime_degraded = bool(
        payload.get("runtime_degraded", runtime.get("runtime_degraded", False))
    )
    category = _as_text(
        payload.get("runtime_failure_category")
        or runtime.get("runtime_failure_category")
    )
    return runtime_degraded and category == "tool_execution_error"


def _extract_human_review_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw = _as_record(payload.get("human_review"))
    if raw:
        required = raw.get("required")
        if required is False:
            return None
        return {
            "required": True,
            "reason": _as_text(raw.get("reason")),
            "repair_mode": _as_text(raw.get("repair_mode")),
            "repair_target": _as_text(raw.get("repair_target")),
            "repair_steps": list(raw.get("repair_steps") or []),
        }

    recursive_repair = _as_record(payload.get("recursive_repair"))
    if _as_text(recursive_repair.get("repair_mode")) != "needs_human_review":
        return None

    repair_steps = recursive_repair.get("repair_steps")
    normalized_steps = [
        item
        for item in (_as_text(entry) for entry in repair_steps or [])
        if item is not None
    ]
    return {
        "required": True,
        "reason": _as_text(payload.get("final_reasoning"))
        or _as_text(recursive_repair.get("repair_rationale"))
        or _as_text(recursive_repair.get("repair_target")),
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
    if kind == "final":
        if human_review_required:
            return "needs_human_review"
        return "error" if final_event_failed(payload) else "completed"
    if kind == "cancelled":
        return "cancelled"
    return "error"


def _build_fallback_final_artifact(event: StreamEventLike) -> dict[str, Any] | None:
    if event.kind != "final":
        return None
    return {
        "kind": "assistant_response",
        "value": {
            "text": event.text,
            "final_markdown": event.text,
            "summary": event.text,
        },
        "finalization_mode": "RETURN",
    }


def _build_minimum_summary(
    *,
    event: StreamEventLike,
    summary_payload: dict[str, Any],
    warnings: list[Any],
    human_review: dict[str, Any] | None,
) -> dict[str, Any]:
    error_text = event.text if event.kind == "error" else None
    summary = {
        "termination_reason": summary_payload.get("termination_reason") or event.kind,
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
    if human_review_required and normalized in {None, "", "final", "completed"}:
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
    warnings = list(
        summary_payload.get("warnings") or payload.get("guardrail_warnings") or []
    )
    minimum_summary = _build_minimum_summary(
        event=event,
        summary_payload=summary_payload,
        warnings=warnings,
        human_review=human_review,
    )

    if run_result:
        normalized = dict(run_result)
        normalized.setdefault(
            "run_id", run_result.get("run_id") or runtime.get("run_id") or run_id
        )
        normalized.setdefault("runtime_mode", runtime_mode)
        normalized.setdefault("task", run_result.get("task") or request_message)
        normalized["status"] = _resolve_terminal_status(
            existing_status=run_result.get("status"),
            terminal_status=terminal_status,
        )
        normalized["termination_reason"] = _resolve_termination_reason(
            existing_reason=run_result.get("termination_reason")
            or summary_payload.get("termination_reason"),
            event_kind=event.kind,
            human_review_required=human_review is not None,
        )
        normalized.setdefault("duration_ms", summary_payload.get("duration_ms"))
        normalized.setdefault("warnings", warnings)
        nested_summary = _as_record(normalized.get("summary"))
        nested_summary = {**minimum_summary, **nested_summary}
        if summary_payload:
            nested_summary = {**nested_summary, **summary_payload}
        if warnings and not nested_summary.get("warnings"):
            nested_summary["warnings"] = warnings
        if human_review is not None:
            normalized["human_review"] = human_review
            nested_summary["human_review"] = human_review
        normalized["summary"] = nested_summary
        normalized.setdefault(
            "final_artifact",
            payload_final_artifact or _build_fallback_final_artifact(event),
        )
        return normalized

    final_artifact = payload_final_artifact or _build_fallback_final_artifact(event)

    return {
        "run_id": _as_text(runtime.get("run_id")) or run_id,
        "runtime_mode": runtime_mode,
        "task": request_message,
        "status": terminal_status,
        "termination_reason": _resolve_termination_reason(
            existing_reason=summary_payload.get("termination_reason"),
            event_kind=event.kind,
            human_review_required=human_review is not None,
        ),
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
        "human_review": human_review,
    }
