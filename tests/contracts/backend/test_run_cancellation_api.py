"""Run cancellation ownership surface."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.local_scope import LocalScope
from fleet_rlm.chat.turn_lifecycle import BeginTurn, ExecuteTurn
from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.sessions.models import TurnAccess, TurnInput


def _headers(user_id=None, workspace_id=None):
    return {
        "X-Fleet-User-Id": str(user_id or uuid4()),
        "X-Fleet-Workspace-Id": str(workspace_id or uuid4()),
    }


@pytest.mark.asyncio
async def test_cancel_missing_run_returns_404() -> None:
    app = create_testing_app()
    scope = LocalScope()
    user, ws = scope.user_id, scope.workspace_id
    headers = _headers(user, ws)
    with TestClient(app) as client:
        missing = client.put(f"/api/runs/{uuid4()}/cancellation", headers=headers)
        assert missing.status_code == 404
        assert missing.json()["code"] == "run_not_found"


@pytest.mark.asyncio
async def test_cancel_owned_run_records_intent_and_is_idempotent() -> None:
    app = create_testing_app()
    scope = LocalScope()
    user, ws = scope.user_id, scope.workspace_id
    headers = _headers(user, ws)
    with TestClient(app) as client:
        created = client.post("/api/sessions", json={"title": "t"}, headers=headers)
        session_id = UUID(created.json()["id"])
        started = await app.state.turn_lifecycle.begin(
            BeginTurn(TurnAccess(user, ws), session_id, TurnInput("question"), "key-2", uuid4())
        )
        assert isinstance(started, ExecuteTurn)

        r1 = client.put(f"/api/runs/{started.run_id}/cancellation", headers=headers)
        assert r1.status_code == 200
        assert r1.json()["state"] == "requested"

        r2 = client.put(f"/api/runs/{started.run_id}/cancellation", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["state"] == "already_requested"
