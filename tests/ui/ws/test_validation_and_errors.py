"""Validation and error handling tests for websocket routes."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("payload", "expected_substring"),
    [
        ({"type": "invalid_type", "content": "test"}, "Unknown message type"),
        ({"type": "message", "content": ""}, "empty"),
    ],
)
def test_websocket_validation_errors(
    ws_client, websocket_auth_headers, payload: dict[str, str], expected_substring: str
):
    with ws_client.websocket_connect(
        "/api/v1/ws/chat", headers=websocket_auth_headers
    ) as websocket:
        websocket.send_json(payload)
        data = websocket.receive_json()
        assert data["type"] == "error"
        assert expected_substring.lower() in data["message"].lower()


def test_health_endpoint(ws_client):
    resp = ws_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
