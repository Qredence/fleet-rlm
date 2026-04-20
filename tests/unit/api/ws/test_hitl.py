from __future__ import annotations

import asyncio
from typing import Any

from fleet_rlm.api.routers.ws.hitl import handle_resolve_hitl


class _WebSocketStub:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.messages.append(payload)


def _command_response(*, command: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"type": "command_result", "command": command, "result": result}


def test_handle_resolve_hitl_is_no_op_in_simplified_architecture() -> None:
    """HITL resolution is removed; handle_resolve_hitl always returns False."""

    async def scenario() -> None:
        websocket = _WebSocketStub()

        handled = await handle_resolve_hitl(
            websocket=websocket,
            command="resolve_hitl",
            args={"message_id": "hitl-123", "action_label": "Approve"},
            command_response=_command_response,
            session_record={},
        )

        assert handled is False
        assert websocket.messages == []

    asyncio.run(scenario())


def test_handle_resolve_hitl_ignores_other_commands() -> None:
    async def scenario() -> None:
        websocket = _WebSocketStub()

        handled = await handle_resolve_hitl(
            websocket=websocket,
            command="list_documents",
            args={},
            command_response=_command_response,
            session_record=None,
        )

        assert handled is False
        assert websocket.messages == []

    asyncio.run(scenario())

