from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from fleet_rlm.artifacts.errors import ArtifactNotFoundError
from fleet_rlm.artifacts.models import ArtifactCandidate
from fleet_rlm.artifacts.persistent import VolumeArtifactStore
from fleet_rlm.chat.capabilities import assemble_turn_capabilities
from fleet_rlm.chat.commands import ChatTurnCommand
from fleet_rlm.chat.context_builder import OfflineContextBuilder, rebind_turn_context
from fleet_rlm.daytona.paths import VolumePaths
from fleet_rlm.daytona.volume_fs import HostVolumeMirror
from fleet_rlm.daytona.workspace_volume import HostWorkspaceVolumeGateway
from fleet_rlm.files.store import VolumeAttachmentStore
from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
from fleet_rlm.persistence.repositories import SqlAlchemySessionRepository
from fleet_rlm.persistence.repositories.files import (
    SqlAlchemyArtifactRepository,
    SqlAlchemyAttachmentRepository,
)
from fleet_rlm.sessions.checkpoints import StaleCheckpointError


@pytest.mark.asyncio
async def test_attachment_upload_writes_workspace_volume_before_catalog(tmp_path) -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    paths = VolumePaths.from_mount()
    gateway = HostWorkspaceVolumeGateway(tmp_path / "volume", volume_paths=paths)
    repository = SqlAlchemyAttachmentRepository(create_session_factory(engine))
    store = VolumeAttachmentStore(repository, gateway, max_bytes=1024, volume_paths=paths)
    user_id, workspace_id = uuid4(), uuid4()

    ref = await store.upload(
        user_id=user_id,
        workspace_id=workspace_id,
        filename="notes.txt",
        content_type="text/plain",
        data=b"durable",
    )

    assert await store.read_bytes(ref.id, user_id=user_id, workspace_id=workspace_id) == b"durable"
    stored = await repository.get(ref.id, user_id=user_id, workspace_id=workspace_id)
    assert stored.storage_ref == str(paths.attachment_blob_path(ref.id))
    await engine.dispose()


@pytest.mark.asyncio
async def test_attachment_catalog_is_not_written_when_volume_write_fails() -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)

    class FailingGateway:
        async def write_bytes(self, *_args, **_kwargs) -> None:
            raise RuntimeError("volume unavailable")

        async def read_bytes(self, *_args, **_kwargs) -> bytes:
            raise RuntimeError("volume unavailable")

    repository = SqlAlchemyAttachmentRepository(create_session_factory(engine))
    store = VolumeAttachmentStore(repository, FailingGateway(), max_bytes=1024)

    with pytest.raises(RuntimeError, match="volume unavailable"):
        await store.upload(
            user_id=uuid4(),
            workspace_id=uuid4(),
            filename="notes.txt",
            content_type="text/plain",
            data=b"durable",
        )

    assert await repository.count() == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_artifact_metadata_commits_atomically_with_completed_turn(tmp_path) -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    factory = create_session_factory(engine)
    sessions = SqlAlchemySessionRepository(factory)
    artifacts = SqlAlchemyArtifactRepository(factory)
    paths = VolumePaths.from_mount()
    gateway = HostWorkspaceVolumeGateway(tmp_path / "volume", volume_paths=paths)
    artifact_store = VolumeArtifactStore(artifacts, gateway)
    user_id, workspace_id, run_id, artifact_id = (uuid4() for _ in range(4))
    session = await sessions.create(user_id=user_id, workspace_id=workspace_id)
    await sessions.begin_run(session.id, run_id=run_id)
    durable_path = str(paths.artifact_blob_path(artifact_id))
    await gateway.write_bytes(workspace_id, durable_path, b"committed")
    candidate = ArtifactCandidate(
        id=artifact_id,
        user_id=user_id,
        workspace_id=workspace_id,
        session_id=session.id,
        run_id=run_id,
        kind="text",
        title="result",
        media_type="text/plain",
        byte_size=9,
        checksum_sha256="cc962289af2873dd6dad32931554372a7d2d2de5bd5859c8265eb58b5197a88e",
        staging_path="private",
        durable_path=durable_path,
    )

    snapshot = await sessions.commit_completed_turn(
        session.id,
        user_text="make it",
        assistant_text="done",
        run_id=run_id,
        expected_checkpoint_version=0,
        artifact_candidates=(candidate,),
    )

    assert snapshot.session.checkpoint_version == 1
    assert await artifact_store.read_bytes(artifact_id, user_id=user_id, workspace_id=workspace_id) == b"committed"
    assert await artifacts.count() == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_turn_commit_creates_no_artifact_row_but_may_leave_orphan_bytes(tmp_path) -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    factory = create_session_factory(engine)
    sessions = SqlAlchemySessionRepository(factory)
    artifacts = SqlAlchemyArtifactRepository(factory)
    paths = VolumePaths.from_mount()
    gateway = HostWorkspaceVolumeGateway(tmp_path / "volume", volume_paths=paths)
    user_id, workspace_id, run_id, artifact_id = (uuid4() for _ in range(4))
    session = await sessions.create(user_id=user_id, workspace_id=workspace_id)
    await sessions.begin_run(session.id, run_id=run_id)
    durable_path = str(paths.artifact_blob_path(artifact_id))
    await gateway.write_bytes(workspace_id, durable_path, b"orphan")
    candidate = ArtifactCandidate(
        id=artifact_id,
        user_id=user_id,
        workspace_id=workspace_id,
        session_id=session.id,
        run_id=run_id,
        kind="text",
        title=None,
        media_type="text/plain",
        byte_size=6,
        checksum_sha256="88f6811ab5d8fc6d3177f9b7609ae0fcebfda187e5046b62d38bb539e88b74d7",
        staging_path="private",
        durable_path=durable_path,
    )

    with pytest.raises(StaleCheckpointError):
        await sessions.commit_completed_turn(
            session.id,
            user_text="make it",
            assistant_text="done",
            run_id=run_id,
            expected_checkpoint_version=1,
            artifact_candidates=(candidate,),
        )

    assert await artifacts.count() == 0
    with pytest.raises(ArtifactNotFoundError):
        await artifacts.get(artifact_id, user_id=user_id, workspace_id=workspace_id)
    assert await gateway.read_bytes(workspace_id, durable_path) == b"orphan"
    await engine.dispose()


@pytest.mark.asyncio
async def test_live_style_staging_reads_durable_blob_through_run_sandbox_mount(tmp_path) -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    paths = VolumePaths.from_mount()

    class TrackingGateway(HostWorkspaceVolumeGateway):
        read_calls = 0

        async def read_bytes(self, workspace_id, logical_path):
            self.read_calls += 1
            return await super().read_bytes(workspace_id, logical_path)

    volume_root = tmp_path / "volume"
    gateway = TrackingGateway(volume_root, volume_paths=paths)
    store = VolumeAttachmentStore(
        SqlAlchemyAttachmentRepository(create_session_factory(engine)),
        gateway,
        max_bytes=1024,
        volume_paths=paths,
    )
    user_id, workspace_id, session_id, run_id = (uuid4() for _ in range(4))
    attachment = await store.upload(
        user_id=user_id,
        workspace_id=workspace_id,
        filename="run.txt",
        content_type="text/plain",
        data=b"through-run-mount",
    )
    command = ChatTurnCommand(
        user_id=user_id,
        workspace_id=workspace_id,
        session_id=session_id,
        message="read",
        attachment_ids=(attachment.id,),
    )
    context = rebind_turn_context(OfflineContextBuilder().build(command), run_id=run_id)
    run_volume = HostVolumeMirror(
        volume_root / "workspaces" / str(workspace_id),
        volume_paths=paths,
    )

    enriched = await assemble_turn_capabilities(
        context,
        command,
        attachment_store=store,
        volume_fs=run_volume,
        volume_paths=paths,
    )

    assert gateway.read_calls == 0
    assert enriched.file_tool_host is not None
    result = enriched.file_tool_host.read_attachment(str(attachment.id))
    assert result["content"] == "through-run-mount"
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_attachment_uploads_use_distinct_uuid_durable_paths(tmp_path) -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    await create_tables(engine)
    paths = VolumePaths.from_mount()
    gateway = HostWorkspaceVolumeGateway(tmp_path / "volume", volume_paths=paths)
    repository = SqlAlchemyAttachmentRepository(create_session_factory(engine))
    store = VolumeAttachmentStore(repository, gateway, max_bytes=1024, volume_paths=paths)
    user_id, workspace_id = uuid4(), uuid4()
    await SqlAlchemySessionRepository(create_session_factory(engine)).ensure_identity(
        user_id=user_id,
        workspace_id=workspace_id,
    )

    first, second = await asyncio.gather(
        store.upload(
            user_id=user_id,
            workspace_id=workspace_id,
            filename="same.txt",
            content_type="text/plain",
            data=b"first",
        ),
        store.upload(
            user_id=user_id,
            workspace_id=workspace_id,
            filename="same.txt",
            content_type="text/plain",
            data=b"second",
        ),
    )

    first_stored = await repository.get(first.id, user_id=user_id, workspace_id=workspace_id)
    second_stored = await repository.get(second.id, user_id=user_id, workspace_id=workspace_id)
    assert first.id != second.id
    assert first_stored.storage_ref != second_stored.storage_ref
    assert first_stored.storage_ref == str(paths.attachment_blob_path(first.id))
    assert second_stored.storage_ref == str(paths.attachment_blob_path(second.id))
    assert await store.read_bytes(first.id, user_id=user_id, workspace_id=workspace_id) == b"first"
    assert await store.read_bytes(second.id, user_id=user_id, workspace_id=workspace_id) == b"second"
    await engine.dispose()
