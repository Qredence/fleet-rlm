"""B6: ownership surface — cancel authorize, no public stage, artifact session graph."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm_clean.app import create_app
from fleet_rlm_clean.config import Settings
from fleet_rlm_clean.persistence.database import (
    create_async_engine_from_url,
    create_session_factory,
    create_tables,
)
from fleet_rlm_clean.rlm.cancel import (
    RunCancelRegistry,
    get_run_cancel_registry,
    set_run_cancel_registry,
)
from fleet_rlm_clean.sessions.repository import SessionRepository


@pytest.fixture(autouse=True)
def _fresh_cancel_registry():
    reg = RunCancelRegistry()
    set_run_cancel_registry(reg)
    yield reg
    set_run_cancel_registry(RunCancelRegistry())


def _headers(user_id=None, workspace_id=None):
    return {
        "X-Fleet-User-Id": str(user_id or uuid4()),
        "X-Fleet-Workspace-Id": str(workspace_id or uuid4()),
    }


async def _wired_app():
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    app = create_app(settings=Settings(auth_mode="dev"))
    app.state.session_repository = SessionRepository(create_session_factory(engine))
    return app, engine


@pytest.mark.asyncio
async def test_cancel_foreign_or_missing_run_returns_404() -> None:
    app, engine = await _wired_app()
    client = TestClient(app)
    user, ws = uuid4(), uuid4()
    headers = _headers(user, ws)

    missing = client.post(f"/api/runs/{uuid4()}/cancel", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "run not found"

    # Create owned session+run, then cancel as a different workspace → 404
    created = client.post("/api/sessions", json={"title": "t"}, headers=headers)
    assert created.status_code == 201
    session_id = UUID(created.json()["id"])
    repo: SessionRepository = app.state.session_repository
    claim = await repo.claim_turn(session_id)
    foreign = client.post(
        f"/api/runs/{claim.run_id}/cancel",
        headers=_headers(user, uuid4()),
    )
    assert foreign.status_code == 404
    await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_owned_run_records_intent_and_is_idempotent() -> None:
    app, engine = await _wired_app()
    client = TestClient(app)
    user, ws = uuid4(), uuid4()
    headers = _headers(user, ws)
    created = client.post("/api/sessions", json={"title": "t"}, headers=headers)
    session_id = UUID(created.json()["id"])
    repo: SessionRepository = app.state.session_repository
    claim = await repo.claim_turn(session_id)

    r1 = client.post(f"/api/runs/{claim.run_id}/cancel", headers=headers)
    assert r1.status_code == 200
    assert r1.json()["cancelled"] is True
    assert r1.json()["already_cancelled"] is False
    assert get_run_cancel_registry().is_cancelled(claim.run_id) is True

    r2 = client.post(f"/api/runs/{claim.run_id}/cancel", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["already_cancelled"] is True
    await engine.dispose()


def test_cancel_without_db_requires_bound_ownership() -> None:
    app = create_app(settings=Settings(auth_mode="dev"))
    client = TestClient(app)
    user, ws = uuid4(), uuid4()
    headers = _headers(user, ws)
    run_id = uuid4()

    assert client.post(f"/api/runs/{run_id}/cancel", headers=headers).status_code == 404

    get_run_cancel_registry().bind(run_id, user_id=user, workspace_id=ws, session_id=uuid4())
    ok = client.post(f"/api/runs/{run_id}/cancel", headers=headers)
    assert ok.status_code == 200
    assert ok.json()["already_cancelled"] is False

    foreign = client.post(f"/api/runs/{run_id}/cancel", headers=_headers(user, uuid4()))
    assert foreign.status_code == 404


def test_public_stage_route_removed(tmp_path) -> None:
    settings = Settings(upload_root=str(tmp_path / "uploads"), max_upload_bytes=1024)
    app = create_app(settings=settings)
    client = TestClient(app)
    headers = _headers()
    up = client.post(
        "/api/files",
        headers=headers,
        files={"file": ("a.txt", b"hi", "text/plain")},
    )
    assert up.status_code == 200
    file_id = up.json()["id"]
    staged = client.post(
        f"/api/files/{file_id}/stage",
        headers=headers,
        json={"session_id": str(uuid4()), "run_id": str(uuid4())},
    )
    assert staged.status_code == 404


@pytest.mark.asyncio
async def test_artifact_create_requires_owned_session_and_run(tmp_path) -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    settings = Settings(
        artifact_root=str(tmp_path / "arts"),
        max_artifact_bytes=2048,
        auth_mode="dev",
    )
    app = create_app(settings=settings)
    app.state.session_repository = SessionRepository(create_session_factory(engine))
    client = TestClient(app)
    user, ws = uuid4(), uuid4()
    headers = _headers(user, ws)

    foreign = client.post(
        "/api/artifacts",
        headers=headers,
        json={
            "session_id": str(uuid4()),
            "run_id": str(uuid4()),
            "kind": "text",
            "content": "nope",
        },
    )
    assert foreign.status_code == 404

    created = client.post("/api/sessions", json={"title": "t"}, headers=headers)
    session_id = UUID(created.json()["id"])
    repo: SessionRepository = app.state.session_repository
    claim = await repo.claim_turn(session_id)

    mismatch = client.post(
        "/api/artifacts",
        headers=headers,
        json={
            "session_id": str(session_id),
            "run_id": str(uuid4()),
            "kind": "text",
            "content": "nope",
        },
    )
    assert mismatch.status_code == 404

    ok = client.post(
        "/api/artifacts",
        headers=headers,
        json={
            "session_id": str(session_id),
            "run_id": str(claim.run_id),
            "kind": "text",
            "content": "hello",
            "title": "n",
        },
    )
    assert ok.status_code == 200
    artifact_id = ok.json()["id"]
    assert "path" not in ok.json()

    other_ws = client.get(
        f"/api/artifacts/{artifact_id}",
        headers=_headers(user, uuid4()),
    )
    assert other_ws.status_code == 404

    owned = client.get(f"/api/artifacts/{artifact_id}", headers=headers)
    assert owned.status_code == 200
    await engine.dispose()
