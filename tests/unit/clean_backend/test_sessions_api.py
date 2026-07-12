"""HTTP Session CRUD for fleet_rlm_clean."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm_clean.app import create_app
from fleet_rlm_clean.persistence.database import (
    create_async_engine_from_url,
    create_session_factory,
    create_tables,
)
from fleet_rlm_clean.persistence.repositories import SqlAlchemySessionRepository


def _headers(user_id=None, workspace_id=None):
    return {
        "X-Fleet-User-Id": str(user_id or uuid4()),
        "X-Fleet-Workspace-Id": str(workspace_id or uuid4()),
    }


async def _wired_app():
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    app = create_app()
    app.state.session_repository = SqlAlchemySessionRepository(create_session_factory(engine))
    return app, engine


@pytest.mark.asyncio
async def test_sessions_crud_happy_path() -> None:
    app, engine = await _wired_app()
    user, ws = uuid4(), uuid4()
    headers = _headers(user, ws)
    client = TestClient(app)

    created = client.post("/api/sessions", json={"title": "My chat"}, headers=headers)
    assert created.status_code == 201
    body = created.json()
    sid = body["id"]
    assert body["title"] == "My chat"
    assert body["status"] == "active"
    assert body["turn_count"] == 0

    listed = client.get("/api/sessions", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == sid

    got = client.get(f"/api/sessions/{sid}", headers=headers)
    assert got.status_code == 200
    assert got.json()["checkpoint_version"] == 0

    patched = client.patch(
        f"/api/sessions/{sid}",
        json={"title": "Renamed"},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Renamed"

    deleted = client.delete(f"/api/sessions/{sid}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "archived"

    listed_arch = client.get("/api/sessions", params={"status": "archived"}, headers=headers)
    assert listed_arch.json()["total"] == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_sessions_cross_workspace_404() -> None:
    app, engine = await _wired_app()
    user, ws_a, ws_b = uuid4(), uuid4(), uuid4()
    client = TestClient(app)
    created = client.post(
        "/api/sessions",
        json={"title": "private"},
        headers=_headers(user, ws_a),
    )
    sid = created.json()["id"]

    foreign = client.get(f"/api/sessions/{sid}", headers=_headers(user, ws_b))
    assert foreign.status_code == 404
    assert foreign.json()["detail"] == "session not found"

    foreign_list = client.get("/api/sessions", headers=_headers(user, ws_b))
    assert foreign_list.status_code == 200
    assert foreign_list.json()["total"] == 0
    await engine.dispose()


def test_sessions_without_database_503() -> None:
    app = create_app()
    client = TestClient(app)
    response = client.get("/api/sessions", headers=_headers())
    assert response.status_code == 503
    assert "database" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_session_turns_endpoint() -> None:
    app, engine = await _wired_app()
    user, ws = uuid4(), uuid4()
    headers = _headers(user, ws)
    client = TestClient(app)
    sid = client.post("/api/sessions", json={}, headers=headers).json()["id"]

    repo: SqlAlchemySessionRepository = app.state.session_repository
    session_uuid = UUID(sid)
    claim = await repo.claim_turn(session_uuid)
    await repo.commit_completed_turn(
        session_uuid,
        user_text="u",
        assistant_text="a",
        run_id=claim.run_id,
        expected_checkpoint_version=0,
    )

    turns = client.get(f"/api/sessions/{sid}/turns", headers=headers)
    assert turns.status_code == 200
    payload = turns.json()
    assert payload["total"] == 2
    assert payload["items"][0]["role"] == "user"
    assert payload["items"][1]["content"] == "a"
    await engine.dispose()
