"""Human-in-the-loop command helpers for websocket chat."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import WebSocket

from ...orchestration.hitl_policy import resolve_hitl_command

CommandResponseBuilder = Callable[..., dict[str, Any]]


async def handle_resolve_hitl(
    *,
    websocket: WebSocket,
    command: str,
    args: dict[str, Any],
    command_response: CommandResponseBuilder,
) -> bool:
    """Handle the special websocket HITL resolution command when present."""
    resolution = resolve_hitl_command(command=command, args=args)
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
