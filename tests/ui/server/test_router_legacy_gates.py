from __future__ import annotations

from fastapi.testclient import TestClient


def test_tasks_routes_are_gated_when_legacy_sqlite_disabled(
    legacy_disabled_client: TestClient, auth_headers: dict[str, str]
):
    resp = legacy_disabled_client.get("/api/v1/tasks", headers=auth_headers)
    assert resp.status_code == 410
    assert "Legacy SQLite task routes are disabled" in resp.json()["detail"]


def test_sessions_crud_is_gated_but_state_endpoint_remains_available(
    legacy_disabled_client: TestClient, auth_headers: dict[str, str]
):
    sessions_resp = legacy_disabled_client.get("/api/v1/sessions", headers=auth_headers)
    assert sessions_resp.status_code == 410
    assert "Legacy SQLite session routes are disabled" in sessions_resp.json()["detail"]

    state_resp = legacy_disabled_client.get(
        "/api/v1/sessions/state", headers=auth_headers
    )
    assert state_resp.status_code == 200
    assert state_resp.json()["ok"] is True
