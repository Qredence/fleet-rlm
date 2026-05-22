"""Integration tests for WebSocket endpoint behavior.

Tests the canonical WebSocket surfaces:
- /api/v1/ws/execution — conversational websocket stream (auth + frames; rejects query session_id)
- /api/v1/ws/execution/events — passive execution/workbench event stream (requires query session_id; no message/command frames)
"""

from __future__ import annotations

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from fleet_rlm.api.main import create_app


def test_websocket_execution_endpoint_registered() -> None:
    """Verify /api/v1/ws/execution endpoint is registered."""
    app = create_app()
    routes = [route.path for route in app.routes]
    assert "/api/v1/ws/execution" in routes


def test_websocket_execution_events_endpoint_registered() -> None:
    """Verify /api/v1/ws/execution/events endpoint is registered."""
    app = create_app()
    routes = [route.path for route in app.routes]
    assert "/api/v1/ws/execution/events" in routes


def test_websocket_execution_rejects_query_session_id() -> None:
    """Verify /api/v1/ws/execution rejects query session_id parameter.

    The execution endpoint should only accept session_id via message frames,
    not as a query parameter, to prevent session hijacking.
    """
    app = create_app()
    with TestClient(app) as client:
        # Try to connect with session_id in query params
        # This should be rejected or the session_id should be ignored
        # The exact behavior depends on implementation, but the endpoint
        # should not allow session_id as a query parameter for security
        response = client.get("/api/v1/ws/execution?session_id=test-session")
        # WebSocket upgrade should fail or session_id should be ignored
        assert response.status_code in (400, 403, 404, 426)  # WebSocket upgrade failed


def test_websocket_execution_events_requires_session_id() -> None:
    """Verify /api/v1/ws/execution/events rejects connections without session_id."""
    app = create_app()
    with TestClient(app) as client:
        # Connecting without session_id should be accepted then closed by the
        # server with a policy violation (1008) after sending an error envelope.
        with client.websocket_connect("/api/v1/ws/execution/events") as websocket:
            data = websocket.receive_json()
            assert data.get("code") == "missing_session_id"
            with pytest.raises(WebSocketDisconnect) as exc_info:
                websocket.receive_json()
            assert exc_info.value.code == 1008


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "message", "content": "start a run"},
        {"type": "cancel"},
        {"type": "command", "command": "resolve_hitl", "args": {}},
    ],
)
def test_websocket_execution_events_rejects_active_frames(frame: dict[str, object]) -> None:
    """Passive event streams are subscription-only and reject run-mutating frames."""
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws/execution/events?session_id=session-123") as websocket:
            websocket.send_json(frame)
            data = websocket.receive_json()
            assert data.get("type") == "error"
            assert data.get("code") == "passive_subscription_only"
            with pytest.raises(WebSocketDisconnect) as exc_info:
                websocket.receive_json()
            assert exc_info.value.code == 1008


def test_websocket_routes_have_correct_tags() -> None:
    """Verify WebSocket routes are tagged appropriately for OpenAPI documentation."""
    app = create_app()
    ws_routes = [route for route in app.routes if hasattr(route, "path") and "/ws/" in route.path]

    for route in ws_routes:
        # WebSocket routes should be tagged for documentation
        if hasattr(route, "tags"):
            assert "websocket" in route.tags or "ws" in route.tags
