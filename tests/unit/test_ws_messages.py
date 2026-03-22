from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fleet_rlm.api.routers.ws.messages import (
    parse_ws_message_or_send_error,
    resolve_session_identity,
)
from fleet_rlm.api.schemas import WSMessage


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


def test_parse_ws_message_or_send_error_returns_valid_message() -> None:
    websocket = _RecordingWebSocket()

    message = asyncio.run(
        parse_ws_message_or_send_error(
            websocket=websocket,
            raw_payload={"type": "message", "content": "hello"},
        )
    )

    assert message == WSMessage(type="message", content="hello")
    assert websocket.sent == []


def test_parse_ws_message_or_send_error_reports_unknown_type() -> None:
    websocket = _RecordingWebSocket()

    message = asyncio.run(
        parse_ws_message_or_send_error(
            websocket=websocket,
            raw_payload={"type": "unknown", "content": "hello"},
        )
    )

    assert message is None
    assert websocket.sent == [
        {
            "type": "error",
            "message": "Unknown message type: unknown",
        }
    ]


def test_parse_ws_message_or_send_error_reports_daytona_repo_ref_contract() -> None:
    websocket = _RecordingWebSocket()

    message = asyncio.run(
        parse_ws_message_or_send_error(
            websocket=websocket,
            raw_payload={
                "type": "message",
                "content": "hello",
                "runtime_mode": "daytona_pilot",
                "repo_ref": "main",
            },
        )
    )

    assert message is None
    assert websocket.sent == [
        {
            "type": "error",
            "code": "daytona_repo_ref_requires_repo",
            "message": "Daytona repo_ref requires repo_url.",
        }
    ]


def test_resolve_session_identity_preserves_or_creates_session_id(monkeypatch) -> None:
    existing = resolve_session_identity(
        msg=WSMessage(type="message", content="hello", session_id="session-123"),
        workspace_id="workspace-123",
        user_id="user-456",
    )
    assert existing == ("workspace-123", "user-456", "session-123")

    generated_id = uuid.uuid4()
    monkeypatch.setattr(
        "fleet_rlm.api.routers.ws.messages.uuid.uuid4",
        lambda: generated_id,
    )

    generated = resolve_session_identity(
        msg=WSMessage(type="message", content="hello"),
        workspace_id="workspace-123",
        user_id="user-456",
    )
    assert generated == ("workspace-123", "user-456", str(generated_id))
