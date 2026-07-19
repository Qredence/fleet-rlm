from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from fleet_rlm.daytona.orphan_cleanup import cleanup_orphan_bytes
from fleet_rlm.daytona.paths import VolumePaths
from fleet_rlm.daytona.volume_fs import HostVolumeMirror
from fleet_rlm.daytona.workspace_volume import OfflineHostVolumeGateway
from fleet_rlm.persistence.repositories.artifacts import CompletedRun


@pytest.mark.asyncio
async def test_cleanup_removes_only_stale_uncommitted_bytes_and_is_idempotent(tmp_path: Path) -> None:
    paths = VolumePaths.from_mount()
    mirror = HostVolumeMirror(tmp_path, volume_paths=paths)
    gateway = OfflineHostVolumeGateway(mirror)
    workspace_id = uuid4()
    session_id = uuid4()
    completed_run_id = uuid4()
    orphan_run_id = uuid4()
    committed_artifact_id = uuid4()
    orphan_artifact_id = uuid4()
    fresh_artifact_id = uuid4()

    committed_artifact = str(paths.artifact_blob_path(committed_artifact_id))
    orphan_artifact = str(paths.artifact_blob_path(orphan_artifact_id))
    fresh_artifact = str(paths.artifact_blob_path(fresh_artifact_id))
    completed_snapshot = str(paths.run_result_path(session_id, completed_run_id))
    orphan_snapshot = str(paths.run_result_path(session_id, orphan_run_id))
    for path in (committed_artifact, orphan_artifact, fresh_artifact, completed_snapshot, orphan_snapshot):
        mirror.write_bytes(path, b"payload")

    now = datetime.now(UTC)
    stale_time = now.timestamp() - 7200
    for path in (committed_artifact, orphan_artifact, completed_snapshot, orphan_snapshot):
        os.utime(mirror.host_path_for(path), (stale_time, stale_time))

    first = await cleanup_orphan_bytes(
        gateway,
        workspace_id=workspace_id,
        paths=paths,
        committed_storage_refs=(committed_artifact,),
        completed_runs=(CompletedRun(session_id, completed_run_id),),
        now=now,
        grace_period=timedelta(hours=1),
        max_files=20,
    )

    assert first.scanned == 5
    assert first.removed == 2
    assert first.retained == 2
    assert first.skipped_fresh == 1
    assert mirror.exists(committed_artifact)
    assert mirror.exists(completed_snapshot)
    assert mirror.exists(fresh_artifact)
    assert not mirror.exists(orphan_artifact)
    assert not mirror.exists(orphan_snapshot)

    second = await cleanup_orphan_bytes(
        gateway,
        workspace_id=workspace_id,
        paths=paths,
        committed_storage_refs=(committed_artifact,),
        completed_runs=(CompletedRun(session_id, completed_run_id),),
        now=now,
        grace_period=timedelta(hours=1),
        max_files=20,
    )
    assert second.removed == 0


@pytest.mark.asyncio
async def test_cleanup_removes_stale_run_staging_but_preserves_active_run_bytes(tmp_path: Path) -> None:
    paths = VolumePaths.from_mount()
    mirror = HostVolumeMirror(tmp_path, volume_paths=paths)
    gateway = OfflineHostVolumeGateway(mirror)
    workspace_id, session_id, stale_run_id, active_run_id, attachment_id = (uuid4() for _ in range(5))
    stale_artifact = str(paths.run_artifacts_dir(session_id, stale_run_id) / f"{uuid4()}.txt")
    stale_attachment = str(paths.run_attachment_file(session_id, stale_run_id, attachment_id, "note.txt"))
    active_attachment = str(paths.run_attachment_file(session_id, active_run_id, uuid4(), "active.txt"))
    for path in (stale_artifact, stale_attachment, active_attachment):
        mirror.write_bytes(path, b"payload")

    now = datetime.now(UTC)
    stale_time = now.timestamp() - 7200
    for path in (stale_artifact, stale_attachment, active_attachment):
        os.utime(mirror.host_path_for(path), (stale_time, stale_time))

    report = await cleanup_orphan_bytes(
        gateway,
        workspace_id=workspace_id,
        paths=paths,
        committed_storage_refs=(),
        completed_runs=(),
        active_runs=(CompletedRun(session_id, active_run_id),),
        now=now,
        grace_period=timedelta(hours=1),
        max_files=20,
    )

    assert report.removed == 2
    assert not mirror.exists(stale_artifact)
    assert not mirror.exists(stale_attachment)
    assert mirror.exists(active_attachment)


@pytest.mark.asyncio
async def test_artifact_repository_enumerates_workspace_keep_sets() -> None:
    from fleet_rlm.persistence.database import (
        create_async_engine_from_url,
        create_session_factory,
        create_tables,
    )
    from fleet_rlm.persistence.models import ArtifactRow, RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.artifacts import SqlAlchemyArtifactCatalog

    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        factory = create_session_factory(engine)
        user_id, workspace_id, session_id, run_id = (uuid4() for _ in range(4))
        storage_ref = f"/home/daytona/fleet/artifacts/{uuid4()}/blob"
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=user_id),
                    WorkspaceRow(id=workspace_id),
                    SessionRow(id=session_id, user_id=user_id, workspace_id=workspace_id, title="cleanup"),
                    RunRow(
                        id=run_id,
                        session_id=session_id,
                        status="completed",
                        idempotency_key="cleanup-key",
                        input_fingerprint="0" * 64,
                        base_checkpoint_version=0,
                        commit_checkpoint_version=1,
                    ),
                    ArtifactRow(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        user_id=user_id,
                        session_id=session_id,
                        run_id=run_id,
                        checksum_sha256="0" * 64,
                        storage_ref=storage_ref,
                    ),
                )
            )

        catalog = SqlAlchemyArtifactCatalog(factory)
        assert await catalog.list_storage_refs(workspace_id=workspace_id) == frozenset({storage_ref})
        assert await catalog.list_completed_runs(workspace_id=workspace_id) == frozenset(
            {CompletedRun(session_id, run_id)}
        )
    finally:
        await engine.dispose()


def test_host_volume_listing_is_rooted_bounded_and_returns_file_mtimes(tmp_path: Path) -> None:
    paths = VolumePaths.from_mount()
    mirror = HostVolumeMirror(tmp_path, volume_paths=paths)
    artifact_id = uuid4()
    blob = str(paths.artifact_blob_path(artifact_id))
    mirror.write_bytes(blob, b"payload")

    listed = mirror.list_files(str(paths.artifacts_root()), max_depth=2, max_files=1)

    assert len(listed) == 1
    assert listed[0].path == blob
    assert listed[0].modified_at > 0
