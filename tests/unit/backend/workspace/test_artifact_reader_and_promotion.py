"""Committed Artifact read and candidate policy contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest


@dataclass
class _Catalog:
    stored: object

    async def get(self, *, access: object, artifact_id: UUID) -> object:
        del access, artifact_id
        return self.stored


@dataclass
class _Blobs:
    data: bytes
    reads: int = 0

    async def read_bytes(self, workspace_id: UUID, logical_path: str) -> bytes:
        del workspace_id, logical_path
        self.reads += 1
        return self.data


@pytest.mark.asyncio
async def test_artifact_reader_returns_only_integrity_checked_committed_content() -> None:
    from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactRef
    from fleet_rlm.artifacts.reader import ArtifactReader, StoredArtifact

    access = ArtifactAccess(user_id=uuid4(), workspace_id=uuid4())
    ref = ArtifactRef(
        id=uuid4(),
        session_id=uuid4(),
        run_id=uuid4(),
        kind="text",
        title="report",
        media_type="text/plain",
        byte_size=3,
        checksum_sha256="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    )
    reader = ArtifactReader(
        catalog=_Catalog(StoredArtifact(ref=ref, storage_ref="private/artifact")),
        blobs=_Blobs(b"abc"),
    )

    assert await reader.metadata(access, ref.id) == ref
    content = await reader.content(access, ref.id)
    assert content.metadata == ref
    assert content.data == b"abc"
    assert "private/artifact" not in repr(content)


@pytest.mark.asyncio
async def test_artifact_byte_allowance_rejects_before_blob_fetch_and_accepts_exact_limit() -> None:
    from fleet_rlm.artifacts.errors import ArtifactValidationError
    from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactRef
    from fleet_rlm.artifacts.reader import ArtifactReader, StoredArtifact

    ref = ArtifactRef(
        uuid4(),
        uuid4(),
        uuid4(),
        "text",
        None,
        "text/plain",
        3,
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    )
    access = ArtifactAccess(user_id=uuid4(), workspace_id=uuid4())
    blobs = _Blobs(b"abc")
    reader = ArtifactReader(catalog=_Catalog(StoredArtifact(ref, "private/artifact")), blobs=blobs)
    with pytest.raises(ArtifactValidationError, match="byte allowance"):
        await reader.content(access, ref.id, max_bytes=2)
    assert blobs.reads == 0
    assert (await reader.content(access, ref.id, max_bytes=3)).data == b"abc"
    assert blobs.reads == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [True])
async def test_artifact_byte_allowance_rejects_invalid_limits(limit: object) -> None:
    from fleet_rlm.artifacts.errors import ArtifactValidationError
    from fleet_rlm.artifacts.models import ArtifactAccess
    from fleet_rlm.artifacts.reader import ArtifactReader

    blobs = _Blobs(b"unused")
    reader = ArtifactReader(catalog=_Catalog(None), blobs=blobs)
    with pytest.raises(ArtifactValidationError, match="positive integer"):
        await reader.content(ArtifactAccess(uuid4(), uuid4()), uuid4(), max_bytes=limit)
    assert blobs.reads == 0


@pytest.mark.asyncio
async def test_artifact_reader_collapses_corrupt_or_missing_bytes_to_not_found() -> None:
    from fleet_rlm.artifacts.errors import ArtifactNotFoundError
    from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactRef
    from fleet_rlm.artifacts.reader import ArtifactReader, StoredArtifact

    ref = ArtifactRef(uuid4(), uuid4(), uuid4(), "text", None, "text/plain", 3, "a" * 64)
    reader = ArtifactReader(
        catalog=_Catalog(StoredArtifact(ref=ref, storage_ref="private/artifact")),
        blobs=_Blobs(b"wrong"),
    )

    with pytest.raises(ArtifactNotFoundError):
        await reader.content(ArtifactAccess(user_id=uuid4(), workspace_id=uuid4()), ref.id)


@pytest.mark.asyncio
async def test_artifact_reader_rejects_metadata_declared_as_non_text_before_blob_fetch() -> None:
    from fleet_rlm.artifacts.errors import ArtifactValidationError
    from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactRef
    from fleet_rlm.artifacts.reader import ArtifactReader, StoredArtifact

    ref = ArtifactRef(uuid4(), uuid4(), uuid4(), "text", None, "application/octet-stream", 3, "a" * 64)
    blobs = _Blobs(b"abc")
    reader = ArtifactReader(catalog=_Catalog(StoredArtifact(ref, "private/artifact")), blobs=blobs)

    with pytest.raises(ArtifactValidationError, match="supported text type"):
        await reader.content(ArtifactAccess(uuid4(), uuid4()), ref.id, max_bytes=3)
    assert blobs.reads == 0


def test_artifact_promotion_validates_the_complete_owned_candidate_batch() -> None:
    from fleet_rlm.artifacts.errors import ArtifactValidationError
    from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactCandidate
    from fleet_rlm.artifacts.promotion import ArtifactPromotion

    access = ArtifactAccess(user_id=uuid4(), workspace_id=uuid4())
    session_id, run_id = uuid4(), uuid4()
    candidate = ArtifactCandidate(
        id=uuid4(),
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        session_id=session_id,
        run_id=run_id,
        kind="text",
        title=None,
        media_type="text/plain",
        byte_size=3,
        checksum_sha256="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        staging_path="runs/input/candidate.txt",
        durable_path="artifacts/output.txt",
    )
    policy = ArtifactPromotion(max_bytes=8)

    assert policy.validate((candidate,), access=access, session_id=session_id, run_id=run_id) == (candidate,)
    with pytest.raises(ArtifactValidationError):
        policy.validate((candidate, candidate), access=access, session_id=session_id, run_id=run_id)


@pytest.mark.parametrize("field", ["user_id", "workspace_id", "session_id", "run_id"])
def test_artifact_promotion_rejects_candidate_owned_by_another_identity(field: str) -> None:
    from fleet_rlm.artifacts.errors import ArtifactValidationError
    from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactCandidate
    from fleet_rlm.artifacts.promotion import ArtifactPromotion

    access = ArtifactAccess(user_id=uuid4(), workspace_id=uuid4())
    session_id, run_id = uuid4(), uuid4()
    kwargs = dict(
        id=uuid4(),
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        session_id=session_id,
        run_id=run_id,
        kind="text",
        title=None,
        media_type="text/plain",
        byte_size=3,
        checksum_sha256="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        staging_path="runs/input/candidate.txt",
        durable_path="artifacts/output.txt",
    )
    kwargs[field] = uuid4()
    candidate = ArtifactCandidate(**kwargs)
    policy = ArtifactPromotion(max_bytes=8)

    with pytest.raises(ArtifactValidationError, match="ownership is invalid"):
        policy.validate((candidate,), access=access, session_id=session_id, run_id=run_id)


@pytest.mark.parametrize(
    "path",
    ["../outside.txt", "runs/../../escape", "back\\slash.txt", "nul\x00.bin"],
    ids=["dotdot-prefix", "nested-dotdot", "backslash", "nul-byte"],
)
@pytest.mark.parametrize("location", ["staging_path", "durable_path"])
def test_artifact_promotion_rejects_traversal_in_candidate_locations(path: str, location: str) -> None:
    from fleet_rlm.artifacts.errors import ArtifactValidationError
    from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactCandidate
    from fleet_rlm.artifacts.promotion import ArtifactPromotion

    access = ArtifactAccess(user_id=uuid4(), workspace_id=uuid4())
    session_id, run_id = uuid4(), uuid4()
    kwargs = dict(
        id=uuid4(),
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        session_id=session_id,
        run_id=run_id,
        kind="text",
        title=None,
        media_type="text/plain",
        byte_size=3,
        checksum_sha256="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        staging_path="runs/input/candidate.txt",
        durable_path="artifacts/output.txt",
    )
    kwargs[location] = path
    candidate = ArtifactCandidate(**kwargs)
    policy = ArtifactPromotion(max_bytes=8)

    with pytest.raises(ArtifactValidationError, match="location is invalid"):
        policy.validate((candidate,), access=access, session_id=session_id, run_id=run_id)


# --- Local Artifact Catalog Contracts ---
def test_store_create_kinds_checksum_and_reauth(tmp_path: Path) -> None:
    import hashlib

    from fleet_rlm.api.local_scope import LocalScope
    from fleet_rlm.artifacts.errors import ArtifactNotFoundError, ArtifactValidationError
    from tests.support.local_catalog import LocalArtifactCatalog

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
    from tests.support.local_catalog import LocalArtifactCatalog

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
    from tests.support.local_catalog import LocalArtifactCatalog

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

    second = LocalArtifactCatalog(root, max_bytes=1024)
    body = second.read_bytes(ref.id, user_id=user, workspace_id=ws)
    assert body == b"durable payload"
    path_after = second.sandbox_path_for(ref.id, user_id=user, workspace_id=ws)
    assert path_after == path_before
    assert "/home/daytona/fleet/sessions/" in path_after


# --- Attachment and Artifact Durability Contracts ---
class _AttachmentSource:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.data)
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class _AttachmentSink:
    def __init__(self, mirror) -> None:
        self.mirror = mirror

    async def write_private(self, logical_path: str, data: bytes) -> None:
        self.mirror.write_bytes(logical_path, data)

    async def remove_private(self, logical_path: str) -> None:
        self.mirror.remove(logical_path)


class _HybridPaths:
    def __init__(self, volume_paths) -> None:
        self.volume_paths = volume_paths

    def attachment_blob(self, attachment_id):
        return f"{attachment_id}.bin"

    def run_attachment(self, run, attachment_id, filename):
        return str(self.volume_paths.run_attachment_file(run.session_id, run.run_id, attachment_id, filename))


def test_volume_paths_durable_attachment_and_artifact_layout() -> None:
    from fleet_rlm.paths import VolumePaths, as_posix

    paths = VolumePaths.from_mount()
    aid = uuid4()
    art = uuid4()
    sid, rid = uuid4(), uuid4()
    assert as_posix(paths.attachment_blob_path(aid)).endswith(f"/attachments/{aid}/blob")
    assert as_posix(paths.artifact_blob_path(art)).endswith(f"/artifacts/{art}/blob")
    staged = paths.run_attachment_file(sid, rid, aid, "note.txt")
    assert as_posix(staged).startswith("/home/daytona/fleet/sessions/")
    assert str(aid) in as_posix(staged)


def test_daytona_run_attachment_paths_keep_blobs_durable_and_stage_in_scratch() -> None:
    from fleet_rlm.attachments import (
        AttachmentRun,
        AttachmentValidationError,
        DaytonaRunAttachmentPathPolicy,
    )
    from fleet_rlm.paths import VolumePaths, as_posix

    paths = VolumePaths.from_mount()
    policy = DaytonaRunAttachmentPathPolicy(paths)
    session_id, run_id, attachment_id = uuid4(), uuid4(), uuid4()

    assert policy.attachment_blob(attachment_id) == as_posix(paths.attachment_blob_path(attachment_id))
    staged = policy.run_attachment(AttachmentRun(session_id, run_id), attachment_id, "report final.txt")
    assert staged == f"/tmp/fleet/{run_id}/attachments/{attachment_id}/report final.txt"

    with pytest.raises(AttachmentValidationError):
        policy.run_attachment(AttachmentRun(session_id, run_id), attachment_id, "../outside.txt")
    with pytest.raises(AttachmentValidationError):
        policy.run_attachment(AttachmentRun(session_id, run_id), attachment_id, "nested\\outside.txt")


def test_upload_promotes_durable_blob_into_workspace_volume_scope(tmp_path: Path) -> None:
    import asyncio

    from fleet_rlm.attachments import (
        AttachmentAccess,
        AttachmentLifecycleService,
        AttachmentUpload,
        LocalAttachmentBlobGateway,
        LocalAttachmentCatalog,
        LocalAttachmentPathPolicy,
    )

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
            AttachmentUpload("a.txt", "text/plain", _AttachmentSource(b"durable-bytes")),
        )
    )
    assert asyncio.run(module.metadata(access, (ref.id,)))[0] == ref


def test_stager_requires_volume_write_and_materializes_run_path(tmp_path: Path) -> None:
    import asyncio

    from fleet_rlm.attachments import (
        AttachmentAccess,
        AttachmentLifecycleService,
        AttachmentRun,
        AttachmentUpload,
        LocalAttachmentBlobGateway,
        LocalAttachmentCatalog,
    )
    from tests.support.workspace_storage import HostVolumeMirror

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
            AttachmentUpload("in.txt", "text/plain", _AttachmentSource(b"stage-me")),
        )
    )
    session_id, run_id = uuid4(), uuid4()
    prepared = asyncio.run(
        module.prepare_run(
            access,
            (ref.id,),
            AttachmentRun(session_id, run_id),
            _AttachmentSink(mirror),
        )
    )
    staged = prepared.staged[0]
    assert "/runs/" in staged.sandbox_path
    assert mirror.read_bytes(staged.sandbox_path) == b"stage-me"


def test_artifact_store_writes_durable_and_run_scoped_volume_bytes(tmp_path: Path) -> None:
    import hashlib

    from tests.support.local_catalog import LocalArtifactCatalog
    from tests.support.workspace_storage import HostVolumeMirror

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
    from tests.support.local_catalog import LocalArtifactCatalog
    from tests.support.workspace_storage import HostVolumeMirror

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
    store._blob_path(ref.id).unlink()
    assert store.read_bytes(ref.id, user_id=user, workspace_id=ws) == b"persist-me"


def test_host_volume_mirror_rejects_escape(tmp_path: Path) -> None:
    from fleet_rlm.paths import UnsafePathError
    from tests.support.workspace_storage import HostVolumeMirror

    mirror = HostVolumeMirror(tmp_path / "volume")
    with pytest.raises(UnsafePathError):
        mirror.write_bytes("/etc/passwd", b"nope")
