"""impl-12: durable artifacts by logical ID (no live providers)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.local_scope import LocalScope
from fleet_rlm.artifacts.errors import ArtifactNotFoundError, ArtifactValidationError
from fleet_rlm.artifacts.local_catalog import LocalArtifactCatalog
from fleet_rlm.artifacts.safety import parse_kind, sanitize_title, validate_content_size
from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.config import Settings


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
    store = LocalArtifactCatalog(tmp_path, max_bytes=1024)
    scope = LocalScope()
    user, ws = scope.user_id, scope.workspace_id
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
    store = LocalArtifactCatalog(tmp_path, max_bytes=1024)
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
    first = LocalArtifactCatalog(root, max_bytes=1024)
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
    second = LocalArtifactCatalog(root, max_bytes=1024)
    body = second.read_bytes(ref.id, user_id=user, workspace_id=ws)
    assert body == b"durable payload"
    path_after = second.sandbox_path_for(ref.id, user_id=user, workspace_id=ws)
    assert path_after == path_before
    assert "/home/daytona/fleet/sessions/" in path_after


def test_api_get_committed_artifact_has_no_path_leak(tmp_path: Path) -> None:
    settings = Settings(data_root=str(tmp_path), max_artifact_bytes=2048, database_url=None)
    app = create_testing_app(settings=settings)
    scope = LocalScope()
    user, ws = scope.user_id, scope.workspace_id
    headers = {
        "X-Fleet-User-Id": str(user),
        "X-Fleet-Workspace-Id": str(ws),
    }
    with TestClient(app) as client:
        ref = app.state.artifact_reader._catalog._store.create(  # noqa: SLF001
            user_id=user,
            workspace_id=ws,
            session_id=uuid4(),
            run_id=uuid4(),
            kind="markdown",
            title="summary",
            content="## Result\n\nok",
        )
        response = client.get(f"/api/artifacts/{ref.id}", headers=headers)
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

        other = client.get(
            f"/api/artifacts/{ref.id}",
            headers={
                "X-Fleet-User-Id": str(user),
                "X-Fleet-Workspace-Id": str(uuid4()),
            },
        )
        assert other.status_code == 200

        content = client.get(f"/api/artifacts/{ref.id}/content", headers=headers)
        assert content.status_code == 200
        assert content.content == b"## Result\n\nok"
        assert content.headers["content-type"].startswith("text/markdown")
        assert content.headers["content-length"] == str(ref.byte_size)
        assert content.headers["etag"] == f'"{ref.checksum_sha256}"'
        assert content.headers["x-content-type-options"] == "nosniff"
        assert content.headers["content-disposition"] == 'attachment; filename="summary.md"'

        foreign_content = client.get(
            f"/api/artifacts/{ref.id}/content",
            headers={
                "X-Fleet-User-Id": str(user),
                "X-Fleet-Workspace-Id": str(uuid4()),
            },
        )
        assert foreign_content.status_code == 200
        assert foreign_content.content == b"## Result\n\nok"


def test_public_artifact_create_is_removed(tmp_path: Path) -> None:
    app = create_testing_app(settings=Settings(data_root=str(tmp_path), max_artifact_bytes=8, database_url=None))
    client = TestClient(app)
    headers = {
        "X-Fleet-User-Id": str(uuid4()),
        "X-Fleet-Workspace-Id": str(uuid4()),
    }
    response = client.post("/api/artifacts", headers=headers, json={})
    assert response.status_code == 404
    assert "/api/artifacts" not in app.openapi()["paths"]
