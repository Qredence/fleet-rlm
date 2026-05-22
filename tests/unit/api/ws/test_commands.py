"""Tests for constrained websocket command frames."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from fleet_rlm.api.routers.ws.commands import handle_command_with_persist


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


class _Agent:
    interpreter = None

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute_command(self, command: str, args: dict[str, Any]) -> dict[str, Any]:
        self.executed.append((command, args))
        return {"unexpected": True}


def test_websocket_command_rejects_unknown_command_without_dispatch() -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        agent = _Agent()
        persisted: list[bool] = []

        async def local_persist(*, include_volume_save: bool) -> None:
            persisted.append(include_volume_save)

        await handle_command_with_persist(
            websocket=cast(Any, websocket),
            agent=cast(Any, agent),
            payload={"type": "command", "command": "load_document", "args": {"path": "secret.txt"}},
            session_record={},
            identity_rows=None,
            persistence_required=False,
            local_persist=local_persist,
        )

        assert agent.executed == []
        assert websocket.sent[0]["type"] == "command_result"
        assert websocket.sent[0]["result"]["status"] == "error"
        assert websocket.sent[0]["result"]["code"] == "unsupported_command"
        assert persisted == [True]

    asyncio.run(scenario())


def test_websocket_command_accepts_resolve_hitl_schema_without_agent_dispatch() -> None:
    async def scenario() -> None:
        websocket = _RecordingWebSocket()
        agent = _Agent()

        async def local_persist(*, include_volume_save: bool) -> None:
            assert include_volume_save is True

        await handle_command_with_persist(
            websocket=cast(Any, websocket),
            agent=cast(Any, agent),
            payload={
                "type": "command",
                "command": "resolve_hitl",
                "args": {"message_id": "msg-1", "resolution": "approved"},
            },
            session_record={},
            identity_rows=None,
            persistence_required=False,
            local_persist=local_persist,
        )

        assert agent.executed == []
        assert websocket.sent[0]["type"] == "command_result"
        assert websocket.sent[0]["result"] == {
            "status": "ok",
            "message_id": "msg-1",
            "resolution": "approved",
        }

    asyncio.run(scenario())
