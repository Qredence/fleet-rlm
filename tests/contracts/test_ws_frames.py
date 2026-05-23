from __future__ import annotations

import pytest
from fastapi import WebSocketDisconnect


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "message", "content": "start a run"},
        {"type": "cancel"},
        {"type": "command", "command": "resolve_hitl", "args": {}},
    ],
)
def test_passive_execution_stream_rejects_active_frames(no_db_client, frame: dict[str, object]) -> None:
    with no_db_client.websocket_connect("/api/v1/ws/execution/events?session_id=session-123") as websocket:
        websocket.send_json(frame)

        payload = websocket.receive_json()
        assert payload["type"] == "error"
        assert payload["code"] == "passive_subscription_only"

        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008
