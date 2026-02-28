from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import WebSocketRoute


_REQUIRED_HTTP_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/auth/me",
    "/api/v1/chat",
    "/api/v1/runtime/settings",
    "/api/v1/runtime/tests/modal",
    "/api/v1/runtime/tests/lm",
    "/api/v1/runtime/status",
    "/api/v1/tasks",
    "/api/v1/sessions",
    "/api/v1/sessions/state",
    "/api/v1/analytics",
    "/api/v1/search",
    "/api/v1/memory",
    "/api/v1/sandbox",
}

_REQUIRED_WS_PATHS = {
    "/api/v1/ws/chat",
    "/api/v1/ws/execution",
}


def test_required_http_and_websocket_routes_are_registered(
    local_client: TestClient,
) -> None:
    http_paths = {
        route.path for route in local_client.app.routes if isinstance(route, APIRoute)
    }
    ws_paths = {
        route.path
        for route in local_client.app.routes
        if isinstance(route, WebSocketRoute)
    }

    assert _REQUIRED_HTTP_PATHS.issubset(http_paths)
    assert _REQUIRED_WS_PATHS.issubset(ws_paths)


def test_runtime_contract_endpoints_remain_available(
    local_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Keep this contract test offline/deterministic and avoid touching live Modal
    # config files or credentials while checking route availability.
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("DSPY_LM_MODEL", raising=False)
    monkeypatch.delenv("DSPY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DSPY_LM_API_KEY", raising=False)
    monkeypatch.setattr(
        "fleet_rlm.server.routers.runtime.load_modal_config", lambda: {}
    )

    settings = local_client.get("/api/v1/runtime/settings")
    status = local_client.get("/api/v1/runtime/status")
    modal = local_client.post("/api/v1/runtime/tests/modal")
    lm = local_client.post("/api/v1/runtime/tests/lm")

    assert settings.status_code == 200
    assert status.status_code == 200
    assert modal.status_code == 200
    assert lm.status_code == 200


def test_planned_routes_keep_explicit_501(local_client: TestClient) -> None:
    for path in (
        "/api/v1/analytics",
        "/api/v1/search",
        "/api/v1/memory",
        "/api/v1/sandbox",
    ):
        response = local_client.get(path)
        assert response.status_code == 501
        assert "not yet implemented" in response.json()["detail"]


def test_legacy_routes_keep_410_when_disabled(
    legacy_disabled_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    tasks = legacy_disabled_client.get("/api/v1/tasks", headers=auth_headers)
    sessions = legacy_disabled_client.get("/api/v1/sessions", headers=auth_headers)

    assert tasks.status_code == 410
    assert sessions.status_code == 410


def test_legacy_crud_routes_are_marked_deprecated(local_client: TestClient) -> None:
    route_map = {
        (route.path, frozenset(route.methods or [])): route
        for route in local_client.app.routes
        if isinstance(route, APIRoute)
    }

    deprecated_route_keys = {
        ("/api/v1/tasks", frozenset({"POST"})),
        ("/api/v1/tasks", frozenset({"GET"})),
        ("/api/v1/tasks/{task_id}", frozenset({"GET"})),
        ("/api/v1/tasks/{task_id}", frozenset({"PATCH"})),
        ("/api/v1/tasks/{task_id}", frozenset({"DELETE"})),
        ("/api/v1/sessions", frozenset({"POST"})),
        ("/api/v1/sessions", frozenset({"GET"})),
        ("/api/v1/sessions/{session_id}", frozenset({"GET"})),
        ("/api/v1/sessions/{session_id}", frozenset({"PATCH"})),
        ("/api/v1/sessions/{session_id}", frozenset({"DELETE"})),
    }

    for key in deprecated_route_keys:
        route = route_map[key]
        assert route.deprecated is True

    state_route = route_map[("/api/v1/sessions/state", frozenset({"GET"}))]
    assert state_route.deprecated is not True
