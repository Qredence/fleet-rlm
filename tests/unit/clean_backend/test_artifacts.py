"""impl-12: durable artifacts by logical ID (no live providers)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm_clean.app import create_app
from fleet_rlm_clean.artifacts.errors import ArtifactNotFoundError, ArtifactValidationError
from fleet_rlm_clean.artifacts.safety import parse_kind, sanitize_title, validate_content_size
from fleet_rlm_clean.artifacts.store import LocalArtifactStore
from fleet_rlm_clean.config import Settings
from fleet_rlm_clean.persistence.database import (
    create_async_engine_from_url,
    create_session_factory,
    create_tables,
)
from fleet_rlm_clean.sessions.repository import SessionRepository


def test_parse_kind_and_title() -> None:
    assert parse_kind("markdown") == "markdown"
    with pytest.raises(ArtifactValidationError):
        parse_kind("pdf")
    assert sanitize_title("Report v1") == "Report v1"
    with pytest.raises(ArtifactValidationError):
        sanitize_title("../etc/passwd")
    with pytest.raises(ArtifactValidationError):
        sanitize_title("a/b")


def test_validate_content_size() -> None:
    validate_content_size(1, max_bytes=10)
    with pytest.raises(ArtifactValidationError):
        validate_content_size(0, max_bytes=10)
    with pytest.raises(ArtifactValidationError):
        validate_content_size(11, max_bytes=10)


def test_store_create_kinds_checksum_and_reauth(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path, max_bytes=1024)
    user, ws = uuid4(), uuid4()
    session_id, run_id = uuid4(), uuid4()

    text_ref = store.create(
        user_id=user,
        workspace_id=ws,
        session_id=session_id,
        run_id=run_id,
        kind="text",
        content="hello world",
        title="greeting",
    )
    assert text_ref.kind == "text"
    assert text_ref.media_type == "text/plain"
    assert text_ref.byte_size == len(b"hello world")
    assert text_ref.checksum_sha256 == hashlib.sha256(b"hello world").hexdigest()

    md_ref = store.create(
        user_id=user,
        workspace_id=ws,
        session_id=session_id,
        run_id=run_id,
        kind="markdown",
        content="# Title\n\nbody",
    )
    assert md_ref.media_type == "text/markdown"

    json_ref = store.create(
        user_id=user,
        workspace_id=ws,
        session_id=session_id,
        run_id=run_id,
        kind="json",
        content='{"ok": true}',
    )
    assert json_ref.media_type == "application/json"

    with pytest.raises(ArtifactValidationError):
        store.create(
            user_id=user,
            workspace_id=ws,
            session_id=session_id,
            run_id=run_id,
            kind="json",
            content="not-json",
        )

    got = store.get(text_ref.id, user_id=user, workspace_id=ws)
    assert got.id == text_ref.id
    with pytest.raises(ArtifactNotFoundError):
        store.get(text_ref.id, user_id=user, workspace_id=uuid4())
    with pytest.raises(ArtifactNotFoundError):
        store.get(uuid4(), user_id=user, workspace_id=ws)


def test_logical_sandbox_path_run_scoped(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path, max_bytes=1024)
    user, ws = uuid4(), uuid4()
    session_id, run_id = uuid4(), uuid4()
    ref = store.create(
        user_id=user,
        workspace_id=ws,
        session_id=session_id,
        run_id=run_id,
        kind="markdown",
        content="# note",
    )
    path = store.sandbox_path_for(ref.id, user_id=user, workspace_id=ws)
    assert path.startswith("/home/daytona/fleet/sessions/")
    assert str(session_id) in path
    assert str(run_id) in path
    assert "/artifacts/" in path
    assert str(ref.id) in path
    assert path.endswith(".md")
    assert not path.startswith(str(tmp_path))


def test_content_survives_store_reload(tmp_path: Path) -> None:
    """Simulate process restart / sandbox replace: same durable root, re-read bytes."""
    root = tmp_path / "artifacts"
    user, ws = uuid4(), uuid4()
    session_id, run_id = uuid4(), uuid4()
    first = LocalArtifactStore(root, max_bytes=1024)
    ref = first.create(
        user_id=user,
        workspace_id=ws,
        session_id=session_id,
        run_id=run_id,
        kind="text",
        content="durable payload",
    )
    path_before = first.sandbox_path_for(ref.id, user_id=user, workspace_id=ws)

    # New store instance (API restart); same Volume-backed host root
    second = LocalArtifactStore(root, max_bytes=1024)
    body = second.read_bytes(ref.id, user_id=user, workspace_id=ws)
    assert body == b"durable payload"
    path_after = second.sandbox_path_for(ref.id, user_id=user, workspace_id=ws)
    assert path_after == path_before
    assert "/home/daytona/fleet/sessions/" in path_after


@pytest.mark.asyncio
async def test_api_create_get_no_path_leak(tmp_path: Path) -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    settings = Settings(artifact_root=str(tmp_path / "arts"), max_artifact_bytes=2048)
    app = create_app(settings=settings)
    app.state.session_repository = SessionRepository(create_session_factory(engine))
    user, ws = uuid4(), uuid4()
    headers = {
        "X-Fleet-User-Id": str(user),
        "X-Fleet-Workspace-Id": str(ws),
    }
    client = TestClient(app)
    created = client.post("/api/sessions", json={"title": "t"}, headers=headers)
    session_id = UUID(created.json()["id"])
    repo: SessionRepository = app.state.session_repository
    claim = await repo.claim_turn(session_id)
    response = client.post(
        "/api/artifacts",
        headers=headers,
        json={
            "session_id": str(session_id),
            "run_id": str(claim.run_id),
            "kind": "markdown",
            "title": "summary",
            "content": "## Result\n\nok",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "id",
        "session_id",
        "run_id",
        "kind",
        "title",
        "media_type",
        "byte_size",
        "checksum_sha256",
    }
    dumped = json.dumps(body)
    assert "path" not in body
    assert "storage_key" not in dumped
    assert "/home/" not in dumped
    assert tmp_path.as_posix() not in dumped
    assert body["kind"] == "markdown"
    assert body["title"] == "summary"

    artifact_id = body["id"]
    got = client.get(f"/api/artifacts/{artifact_id}", headers=headers)
    assert got.status_code == 200
    assert got.json()["checksum_sha256"] == body["checksum_sha256"]

    other = client.get(
        f"/api/artifacts/{artifact_id}",
        headers={
            "X-Fleet-User-Id": str(user),
            "X-Fleet-Workspace-Id": str(uuid4()),
        },
    )
    assert other.status_code == 404
    await engine.dispose()


@pytest.mark.asyncio
async def test_api_rejects_bad_kind_and_oversize(tmp_path: Path) -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    settings = Settings(artifact_root=str(tmp_path), max_artifact_bytes=8)
    app = create_app(settings=settings)
    app.state.session_repository = SessionRepository(create_session_factory(engine))
    client = TestClient(app)
    user, ws = uuid4(), uuid4()
    headers = {
        "X-Fleet-User-Id": str(user),
        "X-Fleet-Workspace-Id": str(ws),
    }
    created = client.post("/api/sessions", json={"title": "t"}, headers=headers)
    session_id = UUID(created.json()["id"])
    repo: SessionRepository = app.state.session_repository
    claim = await repo.claim_turn(session_id)
    base = {
        "session_id": str(session_id),
        "run_id": str(claim.run_id),
        "content": "abcdefghij",
    }
    bad_kind = client.post(
        "/api/artifacts",
        headers=headers,
        json={**base, "kind": "pdf", "content": "x"},
    )
    assert bad_kind.status_code == 400

    oversize = client.post(
        "/api/artifacts",
        headers=headers,
        json={**base, "kind": "text"},
    )
    assert oversize.status_code == 400
    await engine.dispose()
