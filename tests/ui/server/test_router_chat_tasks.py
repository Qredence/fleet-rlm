from __future__ import annotations

from fastapi.testclient import TestClient


def test_chat_success_includes_deprecation_headers(
    default_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
):
    async def _ok_result(*args, **kwargs):
        return {
            "assistant_response": "ok",
            "trajectory": None,
            "history_turns": 1,
            "guardrail_warnings": [],
        }

    monkeypatch.setattr(
        "fleet_rlm.runners.arun_react_chat_once",
        _ok_result,
    )

    response = default_client.post(
        "/api/v1/chat",
        json={"message": "hello", "trace": False},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Link"] == '</api/v1/ws/chat>; rel="successor-version"'
    assert response.headers["X-Fleet-Removal-Version"] == "0.4.93"


def test_chat_invalid_docs_path_maps_to_400(
    default_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
):
    async def _raise_missing(*args, **kwargs):
        raise FileNotFoundError("Document not found: string")

    monkeypatch.setattr(
        "fleet_rlm.runners.arun_react_chat_once",
        _raise_missing,
    )

    response = default_client.post(
        "/api/v1/chat",
        json={"message": "analyze", "docs_path": "string", "trace": False},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "Document not found" in response.json()["detail"]


def test_chat_value_error_maps_to_400(
    default_client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
):
    async def _raise_bad_input(*args, **kwargs):
        raise ValueError("message cannot be empty")

    monkeypatch.setattr(
        "fleet_rlm.runners.arun_react_chat_once",
        _raise_bad_input,
    )

    response = default_client.post(
        "/api/v1/chat",
        json={"message": " ", "trace": False},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "cannot be empty" in response.json()["detail"]
