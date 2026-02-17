from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from fleet_rlm.server.main import create_app


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
        "fleet_rlm.server.routers.chat.runners.arun_react_chat_once",
        _raise_missing,
    )

    response = client.post(
        "/chat",
        json={"message": "analyze", "docs_path": "string", "trace": False},
    )
    assert response.status_code == 400
    assert "Document not found" in response.json()["detail"]


def test_chat_value_error_maps_to_400(client, monkeypatch):
    async def _raise_bad_input(*args, **kwargs):
        raise ValueError("message cannot be empty")

    monkeypatch.setattr(
        "fleet_rlm.server.routers.chat.runners.arun_react_chat_once",
        _raise_bad_input,
    )

    response = client.post(
        "/chat",
        json={"message": " ", "trace": False},
    )
    assert response.status_code == 400
    assert "cannot be empty" in response.json()["detail"]


def test_tasks_basic_rejects_empty_question(client):
    response = client.post(
        "/tasks/basic",
        json={"task_type": "basic", "question": "   "},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "question is required"


def test_tasks_basic_accepts_non_empty_question(client, monkeypatch):
    monkeypatch.setattr(
        "fleet_rlm.server.routers.tasks.runners.run_basic",
        lambda **kwargs: {"answer": "ok", "question": kwargs["question"]},
    )

    response = client.post(
        "/tasks/basic",
        json={"task_type": "basic", "question": "What is 2+2?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["result"]["answer"] == "ok"
    assert body["result"]["question"] == "What is 2+2?"
