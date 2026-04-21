"""Helpers for terminal WebSocket chat stream events."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.integrations.database import RunStatus

from ...events import ExecutionStep
from .completion import build_execution_completion_summary, final_event_failed
from .helpers import _try_send_json
from ...runtime_services.chat_persistence import ExecutionLifecycleManager
from .types import LocalPersistFn, SessionContext, StreamEventLike

logger = logging.getLogger(__name__)


def build_stream_event_dict(
    *,
    event: StreamEventLike,
    payload: Any,
) -> dict[str, Any]:
    """Serialize one stream event for websocket delivery."""
    return {
        "kind": event.kind,
        "text": event.text,
        "payload": payload,
        "timestamp": event.timestamp.isoformat(),
        "version": 2,
        "event_id": uuid.uuid4().hex,
    }


def _terminal_run_status(event: StreamEventLike) -> RunStatus:
    """Return the authoritative terminal run status for one event."""
    if event.kind in ("cancelled", "done") and (
        isinstance(event.payload, dict) and event.payload.get("cancelled")
    ):
        return RunStatus.CANCELLED
    if event.kind in ("final", "done"):
        payload = event.payload if isinstance(event.payload, dict) else {}
        return RunStatus.FAILED if final_event_failed(payload) else RunStatus.COMPLETED
    return RunStatus.FAILED


async def handle_terminal_stream_event(
    *,
    websocket: WebSocket,
    lifecycle: ExecutionLifecycleManager,
    event: StreamEventLike,
    event_dict: dict[str, Any],
    step: ExecutionStep | None,
    persist_session_state: LocalPersistFn,
    request_message: str,
    orchestration_session: SessionContext | None = None,
) -> None:
    """Handle terminal websocket events: persist, complete lifecycle, send.

    ``orchestration_session`` is retained for API compatibility but the
    simplified architecture has no HITL/checkpoint logic.
    """
    summary = build_execution_completion_summary(
        event=event,
        request_message=request_message,
        run_id=lifecycle.run_id,
    )

    if event.kind in ("final", "done"):
        try:
            await persist_session_state(include_volume_save=True)
        except Exception:
            logger.debug(
                "Failed to persist session state before final event; continuing",
                exc_info=True,
            )
        await lifecycle.complete_run(
            _terminal_run_status(event),
            step=step,
            summary=summary,
        )
        if not await _try_send_json(websocket, {"type": "event", "data": event_dict}):
            raise WebSocketDisconnect(code=1001)
        return

    if not await _try_send_json(websocket, {"type": "event", "data": event_dict}):
        raise WebSocketDisconnect(code=1001)

    try:
        await persist_session_state(include_volume_save=True)
    except Exception:
        logger.debug(
            "Failed to persist session state after %s event; completing run anyway",
            event.kind,
            exc_info=True,
        )

    error_json: dict[str, Any] | None = (
        {"error": event.text, "kind": event.kind} if event.kind == "error" else None
    )
    await lifecycle.complete_run(
        _terminal_run_status(event),
        step=step,
        error_json=error_json,
        summary=summary,
    )
