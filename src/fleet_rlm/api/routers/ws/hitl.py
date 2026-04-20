"""HITL resolution stub — HITL removed in the simplified architecture."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import WebSocket

CommandResponseBuilder = Callable[..., dict[str, Any]]


async def handle_resolve_hitl(
    *,
    websocket: WebSocket,
    command: str,
    args: dict[str, Any],
    command_response: CommandResponseBuilder,
    session_record: dict[str, Any] | None = None,
) -> bool:
    """HITL resolution is not supported in the simplified architecture."""
    return False
