"""impl-11: attachment upload, reauth, and staging (no live providers)."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm_clean.app import create_app
from fleet_rlm_clean.config import Settings
from fleet_rlm_clean.daytona.volume_fs import HostVolumeMirror
from fleet_rlm_clean.files.errors import AttachmentNotFoundError, AttachmentValidationError
from fleet_rlm_clean.files.safety import sanitize_filename, validate_upload_size
from fleet_rlm_clean.files.staging import AttachmentStager
from fleet_rlm_clean.files.uploads import LocalAttachmentStore


def test_sanitize_filename_rejects_paths() -> None:
    assert sanitize_filename("note.txt") == "note.txt"
    with pytest.raises(AttachmentValidationError):
        sanitize_filename("../etc/passwd")
    with pytest.raises(AttachmentValidationError):
        sanitize_filename("/abs/path.txt")
    with pytest.raises(AttachmentValidationError):
        sanitize_filename("")


def test_validate_upload_size() -> None:
    validate_upload_size(1, max_bytes=10)
    with pytest.raises(AttachmentValidationError):
        validate_upload_size(0, max_bytes=10)
    with pytest.raises(AttachmentValidationError):
        validate_upload_size(11, max_bytes=10)


def test_local_store_upload_and_reauth(tmp_path: Path) -> None:
    store = LocalAttachmentStore(tmp_path, max_bytes=1024)
    user, ws = uuid4(), uuid4()
    ref = store.upload(
        user_id=user,
        workspace_id=ws,
        filename="hello.txt",
        content_type="text/plain",
        data=b"hello world",
    )
    assert ref.filename == "hello.txt"
    assert ref.byte_size == 11
    assert len(ref.checksum_sha256) == 64
    got = store.get(ref.id, user_id=user, workspace_id=ws)
    assert got.id == ref.id
    with pytest.raises(AttachmentNotFoundError):
        store.get(ref.id, user_id=uuid4(), workspace_id=ws)
    with pytest.raises(AttachmentNotFoundError):
        store.get(uuid4(), user_id=user, workspace_id=ws)


def test_stage_returns_fleet_sandbox_path_only(tmp_path: Path) -> None:
    mirror = HostVolumeMirror(tmp_path / "volume")
    store = LocalAttachmentStore(
        tmp_path / "blobs",
        max_bytes=1024,
        volume_fs=mirror,
        volume_paths=mirror.volume_paths,
    )
    stager = AttachmentStager(store, volume_fs=mirror, volume_paths=mirror.volume_paths)
    user, ws = uuid4(), uuid4()
    ref = store.upload(
        user_id=user,
        workspace_id=ws,
        filename="doc.txt",
        content_type="text/plain",
        data=b"payload",
    )
    session_id, run_id = uuid4(), uuid4()
    staged = stager.stage(
        ref.id,
        user_id=user,
        workspace_id=ws,
        session_id=session_id,
        run_id=run_id,
    )
    assert staged.sandbox_path.startswith("/home/daytona/fleet/sessions/")
    assert str(ref.id) in staged.sandbox_path
    assert "doc.txt" in staged.sandbox_path
    assert not staged.sandbox_path.startswith(str(tmp_path))
    # Workspace Volume Scope mirror has the staged bytes
    assert mirror.exists(staged.sandbox_path)
    assert mirror.read_bytes(staged.sandbox_path) == b"payload"


def test_api_upload_get_and_stage_no_path_leak(tmp_path: Path) -> None:
    settings = Settings(upload_root=str(tmp_path / "uploads"), max_upload_bytes=1024)
    app = create_app(settings=settings)
    user, ws = uuid4(), uuid4()
    headers = {
        "X-Fleet-User-Id": str(user),
        "X-Fleet-Workspace-Id": str(ws),
    }
    client = TestClient(app)
    response = client.post(
        "/api/files",
        headers=headers,
        files={"file": ("readme.md", b"# hi", "text/markdown")},
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "id",
        "filename",
        "content_type",
        "byte_size",
        "checksum_sha256",
    }
    assert "path" not in body
    assert "/home/" not in json.dumps(body)
    assert tmp_path.as_posix() not in json.dumps(body)

    file_id = body["id"]
    got = client.get(f"/api/files/{file_id}", headers=headers)
    assert got.status_code == 200
    assert got.json()["filename"] == "readme.md"

    # wrong workspace
    other = client.get(
        f"/api/files/{file_id}",
        headers={
            "X-Fleet-User-Id": str(user),
            "X-Fleet-Workspace-Id": str(uuid4()),
        },
    )
    assert other.status_code == 404

    staged = client.post(
        f"/api/files/{file_id}/stage",
        headers=headers,
        json={"session_id": str(uuid4()), "run_id": str(uuid4())},
    )
    assert staged.status_code == 200
    sp = staged.json()
    assert sp["sandbox_path"].startswith("/home/daytona/fleet/")
    assert tmp_path.as_posix() not in sp["sandbox_path"]


def test_api_rejects_oversize(tmp_path: Path) -> None:
    settings = Settings(upload_root=str(tmp_path), max_upload_bytes=4)
    app = create_app(settings=settings)
    client = TestClient(app)
    response = client.post(
        "/api/files",
        headers={
            "X-Fleet-User-Id": str(uuid4()),
            "X-Fleet-Workspace-Id": str(uuid4()),
        },
        files={"file": ("big.bin", b"12345", "application/octet-stream")},
    )
    assert response.status_code == 400
