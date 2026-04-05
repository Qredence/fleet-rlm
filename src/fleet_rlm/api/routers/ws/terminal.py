"""Helpers for terminal WebSocket chat stream events."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.integrations.database import RunStatus
from fleet_rlm.runtime.models import StreamEvent

from ...execution import ExecutionStep
from .completion import build_execution_completion_summary
from .helpers import _try_send_json
from .lifecycle import ExecutionLifecycleManager
from .types import LocalPersistFn

logger = logging.getLogger(__name__)


def _final_run_status(event: StreamEvent) -> RunStatus:
    payload = event.payload if isinstance(event.payload, dict) else {}
    runtime_payload = payload.get("runtime")
    runtime = runtime_payload if isinstance(runtime_payload, dict) else {}
    runtime_degraded = bool(
        payload.get("runtime_degraded", runtime.get("runtime_degraded", False))
    )
    runtime_failure_category = str(
        payload.get("runtime_failure_category")
        or runtime.get("runtime_failure_category")
        or ""
    ).strip()
    if runtime_degraded and runtime_failure_category == "tool_execution_error":
        return RunStatus.FAILED
    return RunStatus.COMPLETED


def build_stream_event_dict(
    *,
    event: StreamEvent,
    payload: Any,
) -> dict[str, Any]:
    """Serialize one stream event for websocket delivery."""
    return {
        "kind": event.kind,
        "text": event.text,
        "payload": payload,
        "timestamp": event.timestamp.isoformat(),
        "version": 2,
        "event_id": str(uuid.uuid4()),
    }


async def handle_terminal_stream_event(
    *,
    websocket: WebSocket,
    lifecycle: ExecutionLifecycleManager,
    event: StreamEvent,
    event_dict: dict[str, Any],
    step: ExecutionStep | None,
    persist_session_state: LocalPersistFn,
    request_message: str,
) -> None:
    """Handle final/cancelled/error websocket events without changing ordering."""
    if event.kind == "final":
        try:
            await persist_session_state(include_volume_save=True)
        except Exception:
            # Preserve the happy-path websocket response even if session persistence
            # regresses during cleanup. The final answer is already available, so
            # continue run completion and delivery instead of downgrading to a
            # transport error.
            logger.exception(
                "Failed to persist session state before final event; continuing"
            )
        await lifecycle.complete_run(
            _final_run_status(event),
            step=step,
            summary=build_execution_completion_summary(
                event=event,
                request_message=request_message,
                run_id=lifecycle.run_id,
            ),
        )
        if not await _try_send_json(websocket, {"type": "event", "data": event_dict}):
            raise WebSocketDisconnect(code=1001)
        return

    if not await _try_send_json(websocket, {"type": "event", "data": event_dict}):
        raise WebSocketDisconnect(code=1001)

    try:
        await persist_session_state(include_volume_save=True)
    except Exception:
        # Keep terminal ordering intact: send the event first, then try to persist,
        # but always finish the lifecycle record even if persistence fails.
        logger.exception(
            "Failed to persist session state after %s event; completing run anyway",
            event.kind,
        )

    status = RunStatus.CANCELLED if event.kind == "cancelled" else RunStatus.FAILED
    error_json = (
        {"error": event.text, "kind": event.kind} if event.kind == "error" else None
    )
    await lifecycle.complete_run(
        status,
        step=step,
        error_json=error_json,
        summary=build_execution_completion_summary(
            event=event,
            request_message=request_message,
            run_id=lifecycle.run_id,
        ),
    )
