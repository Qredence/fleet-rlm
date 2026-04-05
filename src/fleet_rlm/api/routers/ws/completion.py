"""Pure helpers for terminal WebSocket execution summary shaping."""

from __future__ import annotations

from typing import Any

from fleet_rlm.runtime.models import StreamEvent


def _as_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_text(value: Any) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    return None


def _final_event_failed(payload: dict[str, Any]) -> bool:
    runtime = _as_record(payload.get("runtime"))
    runtime_degraded = bool(
        payload.get("runtime_degraded", runtime.get("runtime_degraded", False))
    )
    category = _as_text(
        payload.get("runtime_failure_category")
        or runtime.get("runtime_failure_category")
    )
    return runtime_degraded and category == "tool_execution_error"


def _canonical_run_status(kind: str, payload: dict[str, Any]) -> str:
    if kind == "final":
        return "error" if _final_event_failed(payload) else "completed"
    if kind == "cancelled":
        return "cancelled"
    return "error"


def _build_fallback_final_artifact(event: StreamEvent) -> dict[str, Any] | None:
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
    event: StreamEvent,
    summary_payload: dict[str, Any],
    warnings: list[Any],
) -> dict[str, Any]:
    error_text = event.text if event.kind == "error" else None
    return {
        "termination_reason": summary_payload.get("termination_reason") or event.kind,
        "duration_ms": summary_payload.get("duration_ms"),
        "warnings": warnings,
        "error": error_text,
    }


def build_execution_completion_summary(
    *,
    event: StreamEvent,
    request_message: str,
    run_id: str,
) -> dict[str, Any]:
    """Build the canonical execution summary payload from a terminal event."""
    payload = _as_record(event.payload)
    runtime = _as_record(payload.get("runtime"))
    run_result = _as_record(payload.get("run_result"))
    summary_payload = _as_record(payload.get("summary"))
    payload_final_artifact = _as_record(payload.get("final_artifact"))
    runtime_mode = (
        _as_text(payload.get("runtime_mode"))
        or _as_text(runtime.get("runtime_mode"))
        or _as_text(run_result.get("runtime_mode"))
        or "modal_chat"
    )
    terminal_status = _canonical_run_status(event.kind, payload)
    warnings = list(
        summary_payload.get("warnings") or payload.get("guardrail_warnings") or []
    )
    minimum_summary = _build_minimum_summary(
        event=event,
        summary_payload=summary_payload,
        warnings=warnings,
    )

    if run_result:
        normalized = dict(run_result)
        normalized.setdefault(
            "run_id", run_result.get("run_id") or runtime.get("run_id") or run_id
        )
        normalized.setdefault("runtime_mode", runtime_mode)
        normalized.setdefault("task", run_result.get("task") or request_message)
        normalized.setdefault("status", terminal_status)
        normalized.setdefault(
            "termination_reason",
            summary_payload.get("termination_reason") or event.kind,
        )
        normalized.setdefault("duration_ms", summary_payload.get("duration_ms"))
        normalized.setdefault("warnings", warnings)
        nested_summary = _as_record(normalized.get("summary"))
        nested_summary = {**minimum_summary, **nested_summary}
        if summary_payload:
            nested_summary = {**nested_summary, **summary_payload}
        if warnings and not nested_summary.get("warnings"):
            nested_summary["warnings"] = warnings
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
        "termination_reason": summary_payload.get("termination_reason") or event.kind,
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
    }
