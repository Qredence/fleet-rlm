"""Event emission and persistence helpers for streaming turns."""

from __future__ import annotations

from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.agent_host import OrchestrationSessionContext
from fleet_rlm.integrations.database import RunStatus
from fleet_rlm.integrations.observability.mlflow_context import (
    merge_trace_result_metadata as _merge_trace_result_metadata,
)
from fleet_rlm.worker import WorkspaceEvent

from ...events import ExecutionStepBuilder
from .helpers import _try_send_json
from ...runtime_services.chat_persistence import ExecutionLifecycleManager
from .terminal import build_stream_event_dict, handle_terminal_stream_event
from .types import LocalPersistFn, StreamEventLike


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


def _is_terminal_transport_event(event: StreamEventLike) -> bool:
    """Return websocket-terminal semantics for worker and legacy runtime events."""

    return bool(getattr(event, "terminal", False)) or event.kind in {
        "final",
        "cancelled",
        "error",
    }


async def _emit_stream_event(
    *,
    websocket: WebSocket,
    lifecycle: ExecutionLifecycleManager,
    step_builder: ExecutionStepBuilder,
    event: WorkspaceEvent | StreamEventLike,
    orchestration_session: OrchestrationSessionContext | None = None,
    persist_session_state: LocalPersistFn,
    request_message: str,
) -> None:
    lifecycle.raise_if_persistence_error()
    payload = event.payload
    if event.kind == "final":
        payload = merge_trace_result_metadata(
            payload if isinstance(payload, dict) else None,
            response_preview=event.text,
            trace_metadata=_runtime_trace_metadata(
                payload if isinstance(payload, dict) else None
            ),
        )
    event_dict = build_stream_event_dict(event=event, payload=payload)
    is_terminal_event = _is_terminal_transport_event(event)
    if not is_terminal_event:
        if not await _try_send_json(websocket, {"type": "event", "data": event_dict}):
            raise WebSocketDisconnect(code=1001)

    event_timestamp = event.timestamp.timestamp()
    step = step_builder.from_stream_event(
        kind=event.kind,
        text=event.text,
        payload=payload,
        timestamp=event_timestamp,
    )
    if step is not None:
        await lifecycle.emit_step(step)
        await lifecycle.persist_step(step)
        lifecycle.raise_if_persistence_error()

    if is_terminal_event:
        await handle_terminal_stream_event(
            websocket=websocket,
            lifecycle=lifecycle,
            event=event,
            event_dict=event_dict,
            step=step,
            orchestration_session=orchestration_session,
            persist_session_state=persist_session_state,
            request_message=request_message,
        )
        return


async def complete_run_if_needed(
    lifecycle: ExecutionLifecycleManager,
) -> None:
    """Complete the run if it has not already been marked complete."""
    if not lifecycle.run_completed:
        lifecycle.raise_if_persistence_error()
        await lifecycle.complete_run(RunStatus.COMPLETED)
