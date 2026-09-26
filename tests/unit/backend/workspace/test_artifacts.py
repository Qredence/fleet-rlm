"""Durable local Artifact catalog behavior without live providers."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from fleet_rlm.api.local_scope import LocalScope
from fleet_rlm.artifacts.errors import ArtifactNotFoundError, ArtifactValidationError
from fleet_rlm.artifacts.local_catalog import LocalArtifactCatalog
from fleet_rlm.artifacts.safety import parse_kind, sanitize_title, validate_content_size


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
