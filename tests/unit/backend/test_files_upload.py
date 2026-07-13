"""impl-11: attachment upload, reauth, and staging (no live providers)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.app import create_app
from fleet_rlm.config import Settings
from fleet_rlm.daytona.paths import VolumePaths
from fleet_rlm.daytona.volume_fs import HostVolumeMirror
from fleet_rlm.files.errors import AttachmentNotFoundError, AttachmentValidationError
from fleet_rlm.files.lifecycle import AttachmentModule
from fleet_rlm.files.local_catalog import LocalAttachmentBlobGateway, LocalAttachmentCatalog
from fleet_rlm.files.models import AttachmentAccess, AttachmentRun, AttachmentUpload
from fleet_rlm.files.paths import LocalAttachmentPathPolicy
from fleet_rlm.files.safety import sanitize_filename, validate_upload_size


class _Source:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.data)
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class _Sink:
    def __init__(self, mirror: HostVolumeMirror) -> None:
        self.mirror = mirror

    async def read_private(self, logical_path: str) -> bytes:
        return self.mirror.read_bytes(logical_path)

    async def write_private(self, logical_path: str, data: bytes) -> None:
        self.mirror.write_bytes(logical_path, data)

    async def remove_private(self, logical_path: str) -> None:
        self.mirror.remove(logical_path)


class _HybridPaths:
    def __init__(self, volume_paths: VolumePaths) -> None:
        self.volume_paths = volume_paths

    def attachment_blob(self, attachment_id):
        return f"{attachment_id}.bin"

    def run_attachment(self, run, attachment_id, filename):
        return str(self.volume_paths.run_attachment_file(run.session_id, run.run_id, attachment_id, filename))


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
    module = AttachmentModule(
        catalog=LocalAttachmentCatalog(tmp_path),
        blobs=LocalAttachmentBlobGateway(tmp_path),
        paths=LocalAttachmentPathPolicy(tmp_path),
        max_bytes=1024,
    )
    user, ws = uuid4(), uuid4()
    access = AttachmentAccess(user, ws)
    ref = asyncio.run(
        module.upload(
            access,
            AttachmentUpload("hello.txt", "text/plain", _Source(b"hello world")),
        )
    )
    assert ref.filename == "hello.txt"
    assert ref.byte_size == 11
    assert len(ref.checksum_sha256) == 64
    got = asyncio.run(module.metadata(access, (ref.id,)))[0]
    assert got.id == ref.id
    with pytest.raises(AttachmentNotFoundError):
        asyncio.run(module.metadata(AttachmentAccess(uuid4(), ws), (ref.id,)))
    with pytest.raises(AttachmentNotFoundError):
        asyncio.run(module.metadata(access, (uuid4(),)))


def test_stage_returns_fleet_sandbox_path_only(tmp_path: Path) -> None:
    mirror = HostVolumeMirror(tmp_path / "volume")
    module = AttachmentModule(
        catalog=LocalAttachmentCatalog(tmp_path / "blobs"),
        blobs=LocalAttachmentBlobGateway(tmp_path / "blobs"),
        paths=_HybridPaths(VolumePaths.from_mount()),
        max_bytes=1024,
    )
    user, ws = uuid4(), uuid4()
    access = AttachmentAccess(user, ws)
    ref = asyncio.run(
        module.upload(
            access,
            AttachmentUpload("doc.txt", "text/plain", _Source(b"payload")),
        )
    )
    session_id, run_id = uuid4(), uuid4()
    prepared = asyncio.run(
        module.prepare_run(
            access,
            (ref.id,),
            AttachmentRun(session_id, run_id),
            _Sink(mirror),
        )
    )
    staged = prepared.staged[0]
    assert staged.sandbox_path.startswith("/home/daytona/fleet/sessions/")
    assert str(ref.id) in staged.sandbox_path
    assert "doc.txt" in staged.sandbox_path
    assert not staged.sandbox_path.startswith(str(tmp_path))
    # Workspace Volume Scope mirror has the staged bytes
    assert mirror.exists(staged.sandbox_path)
    assert mirror.read_bytes(staged.sandbox_path) == b"payload"


def test_api_upload_get_no_path_leak_and_no_public_stage(tmp_path: Path) -> None:
    settings = Settings(data_root=str(tmp_path), max_upload_bytes=1024)
    app = create_app(settings=settings)
    user, ws = uuid4(), uuid4()
    headers = {
        "X-Fleet-User-Id": str(user),
        "X-Fleet-Workspace-Id": str(ws),
    }
    client = TestClient(app)
    response = client.post(
        "/api/attachments",
        headers=headers,
        files={"attachment": ("readme.md", b"# hi", "text/markdown")},
    )
    assert response.status_code == 201
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
    got = client.get(f"/api/attachments/{file_id}", headers=headers)
    assert got.status_code == 200
    assert got.json()["filename"] == "readme.md"

    # wrong workspace
    other = client.get(
        f"/api/attachments/{file_id}",
        headers={
            "X-Fleet-User-Id": str(user),
            "X-Fleet-Workspace-Id": str(uuid4()),
        },
    )
    assert other.status_code == 404

    # Public stage removed (B6)
    staged = client.post(
        f"/api/attachments/{file_id}/stage",
        headers=headers,
        json={"session_id": str(uuid4()), "run_id": str(uuid4())},
    )
    assert staged.status_code == 404


def test_api_rejects_oversize(tmp_path: Path) -> None:
    settings = Settings(data_root=str(tmp_path), max_upload_bytes=4)
    app = create_app(settings=settings)
    client = TestClient(app)
    response = client.post(
        "/api/attachments",
        headers={
            "X-Fleet-User-Id": str(uuid4()),
            "X-Fleet-Workspace-Id": str(uuid4()),
        },
        files={"attachment": ("big.bin", b"12345", "application/octet-stream")},
    )
    assert response.status_code == 400
