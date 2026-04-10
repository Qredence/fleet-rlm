"""Human-in-the-loop command helpers for websocket chat."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket

CommandResponseBuilder = Callable[..., dict[str, Any]]


def _build_hitl_resolved_event(
    *,
    message_id: str,
    action_label: str,
) -> dict[str, Any]:
    return {
        "kind": "hitl_resolved",
        "text": action_label,
        "payload": {
            "message_id": message_id,
            "resolution": action_label,
            "source": "command",
        },
        "version": 1,
        "event_id": str(uuid.uuid4()),
    }


def _build_hitl_resolution_result(
    *,
    message_id: str,
    action_label: str,
) -> dict[str, Any]:
    return {
        "status": "ok",
        "message_id": message_id,
        "resolution": action_label,
    }


async def handle_resolve_hitl(
    *,
    websocket: WebSocket,
    command: str,
    args: dict[str, Any],
    command_response: CommandResponseBuilder,
) -> bool:
    """Handle the special websocket HITL resolution command when present."""
    # TODO(phase-3): move HITL/workflow continuation handling behind the outer
    # orchestration layer instead of keeping it in websocket command transport.
    if command != "resolve_hitl":
        return False

    message_id = str(args.get("message_id", "")).strip()
    action_label = str(args.get("action_label", "")).strip()
    if not message_id or not action_label:
        await websocket.send_json(
            command_response(
                command=command,
                result={
                    "status": "error",
                    "error": "resolve_hitl requires message_id and action_label",
                    "message_id": message_id or None,
                },
            )
        )
        return True

    await websocket.send_json(
        {
            "type": "event",
            "data": _build_hitl_resolved_event(
                message_id=message_id,
                action_label=action_label,
            ),
        }
    )
    await websocket.send_json(
        command_response(
            command=command,
            result=_build_hitl_resolution_result(
                message_id=message_id,
                action_label=action_label,
            ),
        )
    )
    return True
