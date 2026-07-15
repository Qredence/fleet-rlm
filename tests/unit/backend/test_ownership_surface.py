"""B6: ownership surface — cancel authorize, no public stage, artifact session graph."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.local_scope import LocalScope
from fleet_rlm.chat.turn_lifecycle import BeginTurn, ExecuteTurn
from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.config import Settings
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


def test_missing_run_cannot_be_cancelled() -> None:
    app = create_testing_app()
    user, ws = uuid4(), uuid4()
    headers = _headers(user, ws)
    run_id = uuid4()
    with TestClient(app) as client:
        response = client.put(f"/api/runs/{run_id}/cancellation", headers=headers)
    assert response.status_code == 404


def test_public_stage_route_removed(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path), max_upload_bytes=1024, database_url=None)
    app = create_testing_app(settings=settings)
    headers = _headers()
    with TestClient(app) as client:
        up = client.post(
            "/api/attachments",
            headers=headers,
            files={"attachment": ("a.txt", b"hi", "text/plain")},
        )
        assert up.status_code == 201
        file_id = up.json()["id"]
        staged = client.post(
            f"/api/attachments/{file_id}/stage",
            headers=headers,
            json={"session_id": str(uuid4()), "run_id": str(uuid4())},
        )
        assert staged.status_code == 404


def test_public_artifact_create_is_not_an_ownership_surface(tmp_path) -> None:
    app = create_testing_app(settings=Settings(data_root=str(tmp_path), database_url=None))
    client = TestClient(app)
    response = client.post("/api/artifacts", headers=_headers(), json={})
    assert response.status_code == 404
