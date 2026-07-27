"""Attachment and Artifact durability under Workspace Volume Scope."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from fleet_rlm.artifacts.local_catalog import LocalArtifactCatalog
from fleet_rlm.files.host_volume import HostVolumeMirror
from fleet_rlm.files.lifecycle import AttachmentLifecycleService
from fleet_rlm.files.local_catalog import LocalAttachmentBlobGateway, LocalAttachmentCatalog
from fleet_rlm.files.models import AttachmentAccess, AttachmentRun, AttachmentUpload
from fleet_rlm.files.paths import LocalAttachmentPathPolicy
from fleet_rlm.files.volume_paths import UnsafePathError, VolumePaths, as_posix


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


def test_volume_paths_durable_attachment_and_artifact_layout() -> None:
    paths = VolumePaths.from_mount()
    aid = uuid4()
    art = uuid4()
    sid, rid = uuid4(), uuid4()
    assert as_posix(paths.attachment_blob_path(aid)).endswith(f"/attachments/{aid}/blob")
    assert as_posix(paths.artifact_blob_path(art)).endswith(f"/artifacts/{art}/blob")
    staged = paths.run_attachment_file(sid, rid, aid, "note.txt")
    assert as_posix(staged).startswith("/home/daytona/fleet/sessions/")
    assert str(aid) in as_posix(staged)


def test_upload_promotes_durable_blob_into_workspace_volume_scope(tmp_path: Path) -> None:
    module = AttachmentLifecycleService(
        catalog=LocalAttachmentCatalog(tmp_path / "catalog"),
        blobs=LocalAttachmentBlobGateway(tmp_path / "catalog"),
        paths=LocalAttachmentPathPolicy(tmp_path / "catalog"),
        max_bytes=1024,
    )
    user, ws = uuid4(), uuid4()
    access = AttachmentAccess(user, ws)
    ref = asyncio.run(
        module.upload(
            access,
            AttachmentUpload("a.txt", "text/plain", _Source(b"durable-bytes")),
        )
    )
    assert asyncio.run(module.metadata(access, (ref.id,)))[0] == ref


def test_stager_requires_volume_write_and_materializes_run_path(tmp_path: Path) -> None:
    mirror = HostVolumeMirror(tmp_path / "volume")
    module = AttachmentLifecycleService(
        catalog=LocalAttachmentCatalog(tmp_path / "catalog"),
        blobs=LocalAttachmentBlobGateway(tmp_path / "catalog"),
        paths=_HybridPaths(mirror.volume_paths),
        max_bytes=1024,
    )
    user, ws = uuid4(), uuid4()
    access = AttachmentAccess(user, ws)
    ref = asyncio.run(
        module.upload(
            access,
            AttachmentUpload("in.txt", "text/plain", _Source(b"stage-me")),
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
    assert "/runs/" in staged.sandbox_path
    assert mirror.read_bytes(staged.sandbox_path) == b"stage-me"


def test_artifact_store_writes_durable_and_run_scoped_volume_bytes(tmp_path: Path) -> None:
    mirror = HostVolumeMirror(tmp_path / "volume")
    store = LocalArtifactCatalog(
        tmp_path / "catalog",
        max_bytes=1024,
        volume_fs=mirror,
        volume_paths=mirror.volume_paths,
    )
    user, ws = uuid4(), uuid4()
    session_id, run_id = uuid4(), uuid4()
    content = "hello artifact"
    ref = store.create(
        user_id=user,
        workspace_id=ws,
        session_id=session_id,
        run_id=run_id,
        kind="text",
        content=content,
        title="t",
    )
    expected = content.encode("utf-8")
    checksum = hashlib.sha256(expected).hexdigest()
    assert ref.checksum_sha256 == checksum
    durable = store.durable_volume_blob_path(ref.id, user_id=user, workspace_id=ws)
    run_path = store.sandbox_path_for(ref.id, user_id=user, workspace_id=ws)
    assert mirror.read_bytes(durable) == expected
    assert mirror.read_bytes(run_path) == expected
    assert store.read_bytes(ref.id, user_id=user, workspace_id=ws) == expected


def test_artifact_survives_catalog_delete_when_volume_blob_present(tmp_path: Path) -> None:
    """Volume Scope bytes remain readable after host catalog blob is removed."""
    mirror = HostVolumeMirror(tmp_path / "volume")
    store = LocalArtifactCatalog(
        tmp_path / "catalog",
        max_bytes=1024,
        volume_fs=mirror,
        volume_paths=mirror.volume_paths,
    )
    user, ws = uuid4(), uuid4()
    ref = store.create(
        user_id=user,
        workspace_id=ws,
        session_id=uuid4(),
        run_id=uuid4(),
        kind="text",
        content="persist-me",
    )
    # Simulate host catalog blob loss while Volume Scope remains (Sandbox replace case).
    store._blob_path(ref.id).unlink()
    assert store.read_bytes(ref.id, user_id=user, workspace_id=ws) == b"persist-me"


def test_host_volume_mirror_rejects_escape(tmp_path: Path) -> None:

    mirror = HostVolumeMirror(tmp_path / "volume")
    with pytest.raises(UnsafePathError):
        mirror.write_bytes("/etc/passwd", b"nope")
