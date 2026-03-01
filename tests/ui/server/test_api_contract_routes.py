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
    "/api/v1/sessions/state",
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


def test_http_chat_route_is_deprecated(local_client: TestClient) -> None:
    route_map = {
        (route.path, frozenset(route.methods or [])): route
        for route in local_client.app.routes
        if isinstance(route, APIRoute)
    }
    route = route_map[("/api/v1/chat", frozenset({"POST"}))]
    assert route.deprecated is True


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


def test_removed_deprecated_and_planned_routes_absent(
    local_client: TestClient,
) -> None:
    http_paths = {
        route.path for route in local_client.app.routes if isinstance(route, APIRoute)
    }
    removed_paths = {
        "/api/v1/tasks",
        "/api/v1/tasks/{task_id}",
        "/api/v1/sessions",
        "/api/v1/sessions/{session_id}",
        "/api/v1/taxonomy",
        "/api/v1/taxonomy/{path}",
        "/api/v1/analytics",
        "/api/v1/analytics/skills/{skill_id}",
        "/api/v1/search",
        "/api/v1/memory",
        "/api/v1/sandbox",
        "/api/v1/sandbox/file",
    }
    assert removed_paths.isdisjoint(http_paths)
    assert removed_paths.isdisjoint(set(local_client.app.openapi().get("paths", {})))
