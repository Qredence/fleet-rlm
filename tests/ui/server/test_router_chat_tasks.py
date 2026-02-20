from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from fleet_rlm.server.main import create_app

AUTH_HEADERS = {
    "X-Debug-Tenant-Id": "tenant-a",
    "X-Debug-User-Id": "user-a",
    "X-Debug-Email": "alice@example.com",
    "X-Debug-Name": "Alice",
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        "fleet_rlm.server.main.get_planner_lm_from_env",
        lambda *args, **kwargs: "fake-planner-lm",
    )
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_chat_invalid_docs_path_maps_to_400(client, monkeypatch):
    async def _raise_missing(*args, **kwargs):
        raise FileNotFoundError("Document not found: string")

    monkeypatch.setattr(
        "fleet_rlm.runners.arun_react_chat_once",
        _raise_missing,
    )

    response = client.post(
        "/api/v1/chat",
        json={"message": "analyze", "docs_path": "string", "trace": False},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400
    assert "Document not found" in response.json()["detail"]


def test_chat_value_error_maps_to_400(client, monkeypatch):
    async def _raise_bad_input(*args, **kwargs):
        raise ValueError("message cannot be empty")

    monkeypatch.setattr(
        "fleet_rlm.runners.arun_react_chat_once",
        _raise_bad_input,
    )

    response = client.post(
        "/api/v1/chat",
        json={"message": " ", "trace": False},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400
    assert "cannot be empty" in response.json()["detail"]
