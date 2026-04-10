"""Human-in-the-loop command helpers for websocket chat."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import WebSocket

from fleet_rlm.agent_host.hitl_flow import resolve_hitl_continuation
from fleet_rlm.agent_host.sessions import build_orchestration_session_context

CommandResponseBuilder = Callable[..., dict[str, Any]]


async def handle_resolve_hitl(
    *,
    websocket: WebSocket,
    command: str,
    args: dict[str, Any],
    command_response: CommandResponseBuilder,
    session_record: dict[str, Any] | None = None,
) -> bool:
    """Handle the special websocket HITL resolution command when present."""
    resolution = resolve_hitl_continuation(
        command=command,
        args=args,
        session=build_orchestration_session_context(session_record=session_record),
    )
    if resolution is None:
        return False

    if resolution.event_payload is not None:
        await websocket.send_json({"type": "event", "data": resolution.event_payload})

    await websocket.send_json(
        command_response(
            command=command,
            result=resolution.command_result,
        )
    )
    return True
