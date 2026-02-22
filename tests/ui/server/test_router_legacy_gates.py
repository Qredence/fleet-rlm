from __future__ import annotations

from fastapi.testclient import TestClient

from fleet_rlm.server.config import ServerRuntimeConfig
from fleet_rlm.server.main import create_app

AUTH_HEADERS = {
    "X-Debug-Tenant-Id": "tenant-a",
    "X-Debug-User-Id": "user-a",
    "X-Debug-Email": "alice@example.com",
    "X-Debug-Name": "Alice",
}


def _build_client() -> TestClient:
    app = create_app(
        config=ServerRuntimeConfig(
            app_env="local",
            database_required=False,
            enable_legacy_sqlite_routes=False,
        )
    )
    return TestClient(app)


def test_tasks_routes_are_gated_when_legacy_sqlite_disabled():
    with _build_client() as client:
        resp = client.get("/api/v1/tasks", headers=AUTH_HEADERS)
        assert resp.status_code == 410
        assert "Legacy SQLite task routes are disabled" in resp.json()["detail"]


def test_sessions_crud_is_gated_but_state_endpoint_remains_available():
    with _build_client() as client:
        sessions_resp = client.get("/api/v1/sessions", headers=AUTH_HEADERS)
        assert sessions_resp.status_code == 410
        assert (
            "Legacy SQLite session routes are disabled"
            in sessions_resp.json()["detail"]
        )

        state_resp = client.get("/api/v1/sessions/state", headers=AUTH_HEADERS)
        assert state_resp.status_code == 200
        assert state_resp.json()["ok"] is True
