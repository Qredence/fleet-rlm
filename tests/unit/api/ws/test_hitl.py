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


def test_handle_resolve_hitl_emits_event_and_command_result() -> None:
    async def scenario() -> None:
        websocket = _WebSocketStub()

        handled = await handle_resolve_hitl(
            websocket=websocket,
            command="resolve_hitl",
            args={"message_id": "hitl-123", "action_label": "Approve"},
            command_response=_command_response,
        )

        assert handled is True
        assert websocket.messages[0]["type"] == "event"
        assert websocket.messages[0]["data"]["kind"] == "hitl_resolved"
        assert websocket.messages[1]["type"] == "command_result"
        assert websocket.messages[1]["result"]["resolution"] == "Approve"

    asyncio.run(scenario())


def test_handle_resolve_hitl_rejects_missing_args() -> None:
    async def scenario() -> None:
        websocket = _WebSocketStub()

        handled = await handle_resolve_hitl(
            websocket=websocket,
            command="resolve_hitl",
            args={"message_id": ""},
            command_response=_command_response,
        )

        assert handled is True
        assert websocket.messages == [
            {
                "type": "command_result",
                "command": "resolve_hitl",
                "result": {
                    "status": "error",
                    "error": "resolve_hitl requires message_id and action_label",
                    "message_id": None,
                },
            }
        ]

    asyncio.run(scenario())


def test_handle_resolve_hitl_ignores_other_commands() -> None:
    async def scenario() -> None:
        websocket = _WebSocketStub()

        handled = await handle_resolve_hitl(
            websocket=websocket,
            command="list_documents",
            args={},
            command_response=_command_response,
        )

        assert handled is False
        assert websocket.messages == []

    asyncio.run(scenario())
