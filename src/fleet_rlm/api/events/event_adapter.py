"""Typed backend event adapter and websocket projection helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

from fleet_rlm.runtime.execution.streaming_events import is_terminal_stream_event_kind

from .events import BackendEvent, BackendEventKind, ExecutionActorKind, RuntimeEventContext
from .wire_source_type import derive_wire_source_type


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_str(value: Any) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    return None


def _as_actor_kind(value: Any) -> ExecutionActorKind | None:
    text = _as_str(value)
    if text is None:
        return None
    lowered = text.lower()
    if lowered in {"root", "root_rlm", "root-rlm", "root agent"}:
        return "root_rlm"
    if lowered in {"sub_agent", "sub-agent", "subagent"}:
        return "sub_agent"
    if lowered in {"delegate", "rlm_delegate", "rlm-delegate"}:
        return "delegate"
    if lowered == "unknown":
        return "unknown"
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _event_timestamp(raw_timestamp: Any) -> datetime:
    if isinstance(raw_timestamp, datetime):
        return raw_timestamp
    return datetime.now(timezone.utc)


def _derive_backend_kind(kind: str, payload: dict[str, Any]) -> BackendEventKind:
    return cast(BackendEventKind, derive_wire_source_type(kind, payload))


def extract_runtime_context(payload: dict[str, Any]) -> RuntimeEventContext | None:
    runtime_payload = _as_dict(payload.get("runtime"))
    source = runtime_payload if runtime_payload else payload

    context = RuntimeEventContext(
        runtime_mode=_as_str(source.get("runtime_mode")),
        execution_mode=_as_str(source.get("execution_mode")),
        execution_profile=_as_str(source.get("execution_profile")),
        sandbox_id=_as_str(source.get("sandbox_id")),
        child_sandbox_id=_as_str(source.get("child_sandbox_id")),
        volume_name=_as_str(source.get("volume_name")),
        workspace_path=_as_str(source.get("workspace_path")),
        repo_url=_as_str(source.get("repo_url")),
        repo_ref=_as_str(source.get("repo_ref")),
        document_path=_as_str(source.get("document_path") or source.get("loaded_path") or source.get("path")),
        depth=_as_int(source.get("depth")),
        max_depth=_as_int(source.get("max_depth")),
        actor_kind=_as_actor_kind(source.get("actor_kind")),
        actor_id=_as_str(source.get("actor_id")),
        parent_id=_as_str(source.get("parent_id")),
        lane_key=_as_str(source.get("lane_key")),
        llm_call_budget=_as_int(source.get("llm_call_budget")),
    )
    if not any(value is not None for value in context.model_dump().values()):
        return None
    return context


def adapt_stream_event(
    *,
    kind: str,
    text: str,
    payload: dict[str, Any] | None,
    timestamp: Any,
) -> BackendEvent:
    payload_obj = _as_dict(payload)
    return BackendEvent(
        kind=_derive_backend_kind(kind, payload_obj),
        text=text or "",
        payload=payload_obj,
        runtime=extract_runtime_context(payload_obj),
        timestamp=_event_timestamp(timestamp),
    )


def build_chat_event_payload(event: BackendEvent) -> dict[str, Any]:
    payload = dict(event.payload)
    payload.setdefault("source_type", event.kind)
    if event.runtime is not None:
        payload["runtime"] = event.runtime.model_dump(mode="json", exclude_none=True)

    if event.kind == "turn_started":
        kind = "execution_started"
    elif event.kind in {"turn_completed", "turn_failed"}:
        kind = "execution_completed"
        payload.setdefault("status", "failed" if event.kind == "turn_failed" else "completed")
    else:
        kind = "execution_step"

    return {
        "kind": kind,
        "text": event.text,
        "payload": payload,
        "timestamp": event.timestamp.isoformat(),
        "version": 3,
        "event_id": event.timestamp.strftime("%Y%m%d%H%M%S%f"),
    }


def is_terminal_backend_event(event: BackendEvent) -> bool:
    return event.kind in {"turn_completed", "turn_failed"} or is_terminal_stream_event_kind(
        "done" if event.kind == "turn_completed" else "error" if event.kind == "turn_failed" else event.kind
    )
