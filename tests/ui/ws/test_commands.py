"""WebSocket command message behavior tests."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("command_name", "expected_type", "expected_substring"),
    [
        ("list_documents", "command_result", ""),
        ("", "error", "empty"),
    ],
)
def test_websocket_command_validation(
    ws_client,
    websocket_auth_headers,
    command_name: str,
    expected_type: str,
    expected_substring: str,
):
    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(
            {
                "type": "command",
                "command": command_name,
                "args": {},
            }
        )

        data = websocket.receive_json()
        assert data["type"] == expected_type
        if expected_type == "command_result":
            assert data["command"] == "list_documents"
            assert data["result"]["status"] == "ok"
        else:
            assert expected_substring in data["message"].lower()
