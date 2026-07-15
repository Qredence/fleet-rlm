"""Canonical Session Catalog HTTP surface."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.local_scope import LocalScope
from fleet_rlm.chat.turn_lifecycle import BeginTurn, ExecuteTurn
from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.rlm.outcome import RLMOutcome
from fleet_rlm.sessions.models import TurnAccess, TurnInput


def _headers(user_id=None, workspace_id=None):
    return {
        "X-Fleet-User-Id": str(user_id or uuid4()),
        "X-Fleet-Workspace-Id": str(workspace_id or uuid4()),
    }


def test_sessions_crud_happy_path() -> None:
    app = create_testing_app()
    user, workspace = uuid4(), uuid4()
    headers = _headers(user, workspace)
    with TestClient(app) as client:
        created = client.post("/api/sessions", json={"title": "My chat"}, headers=headers)
        assert created.status_code == 201
        body = created.json()
        session_id = body["id"]
        assert body["title"] == "My chat"
        assert body["status"] == "active"
        assert body["checkpoint_version"] == 0

        listed = client.get("/api/sessions", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["id"] == session_id

        patched = client.patch(
            f"/api/sessions/{session_id}",
            json={"title": "Renamed"},
            headers=headers,
        )
        assert patched.status_code == 200
        assert patched.json()["title"] == "Renamed"

        archived = client.patch(
            f"/api/sessions/{session_id}",
            json={"status": "archived"},
            headers=headers,
        )
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"


def test_caller_supplied_identity_headers_do_not_change_local_scope() -> None:
    app = create_testing_app()
    user, workspace_a, workspace_b = uuid4(), uuid4(), uuid4()
    with TestClient(app) as client:
        created = client.post(
            "/api/sessions",
            json={"title": "private"},
            headers=_headers(user, workspace_a),
        )
        session_id = created.json()["id"]

        same_local_scope = client.get(f"/api/sessions/{session_id}", headers=_headers(user, workspace_b))
        assert same_local_scope.status_code == 200
        assert client.get("/api/sessions", headers=_headers(user, workspace_b)).json()["total"] == 1


@pytest.mark.asyncio
async def test_session_turns_are_canonical_ui_messages() -> None:
    from fleet_rlm.rlm.dspy_contract import PredictionResult

    app = create_testing_app()
    scope = LocalScope()
    access = TurnAccess(scope.user_id, scope.workspace_id)
    headers = {}
    with TestClient(app) as client:
        session_id = UUID(client.post("/api/sessions", json={}, headers=headers).json()["id"])
        started = await app.state.turn_lifecycle.begin(
            BeginTurn(access, session_id, TurnInput("question"), "turn-key", uuid4())
        )
        assert isinstance(started, ExecuteTurn)
        await app.state.turn_lifecycle.finish(
            started,
            RLMOutcome(
                "completed",
                prediction=PredictionResult("answer", {"answer": "answer"}, "fleet.default", "1"),
                usage={
                    "iterations": 1,
                    "observed_lm_usage": {"root": {"total_tokens": 3}},
                    "duration_ms": 4,
                },
            ),
        )

        response = client.get(f"/api/sessions/{session_id}/turns", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["next_after_sequence"] is None
        assert [message["role"] for message in payload["items"]] == ["user", "assistant"]
        assert payload["items"][0]["parts"][0]["text"] == "question"
        assert payload["items"][1]["parts"][-1]["text"] == "answer"
