"""Helpers for terminal WebSocket chat stream events."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from fleet_rlm.agent_host.sessions import OrchestrationSessionContext

from ...events import ExecutionStep
from .helpers import _try_send_json
from .lifecycle import ExecutionLifecycleManager
from .types import LocalPersistFn, StreamEventLike


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
        "event_id": str(uuid.uuid4()),
    }


async def handle_terminal_stream_event(
    *,
    websocket: WebSocket,
    lifecycle: ExecutionLifecycleManager,
    event: StreamEventLike,
    event_dict: dict[str, Any],
    step: ExecutionStep | None,
    persist_session_state: LocalPersistFn,
    request_message: str,
    orchestration_session: OrchestrationSessionContext | None = None,
) -> None:
    """Handle terminal websocket events without changing ordering.

    ``orchestration_session`` stays optional for compatibility paths that have
    not restored a resumable session record yet; when present, the outer layer
    updates authoritative workflow/continuation state before persistence.
    """
    from fleet_rlm.agent_host.terminal_flow import apply_terminal_event_policy

    if not await apply_terminal_event_policy(
        lifecycle=lifecycle,
        event=event,
        step=step,
        session=orchestration_session,
        persist_session_state=persist_session_state,
        request_message=request_message,
        send_terminal_event=lambda: _try_send_json(
            websocket, {"type": "event", "data": event_dict}
        ),
    ):
        raise WebSocketDisconnect(code=1001)
