from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from fleet_rlm.artifacts.models import CompletedRun
from fleet_rlm.composition.daytona_workspace import OrphanCleanupReport, cleanup_orphan_bytes
from fleet_rlm.workspace.paths import VolumePaths
from fleet_rlm.workspace.storage import HostVolumeMirror, OfflineHostVolumeGateway


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
async def test_startup_orphan_cleanup_skips_provisioning_for_empty_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.composition.daytona import run_deferred_orphan_cleanup

    async def fail_if_called(*_args: object, **_kwargs: object) -> OrphanCleanupReport:
        raise AssertionError("empty startup cleanup must not provision a sandbox")

    class EmptyCatalog(_FakeArtifactCatalog):
        async def list_storage_refs(self, *, workspace_id: object) -> list[str]:
            self.calls.append(("storage", workspace_id))
            return []

    monkeypatch.setattr("fleet_rlm.composition.daytona_workspace.cleanup_orphan_bytes", fail_if_called)

    await run_deferred_orphan_cleanup(
        object(),
        workspace_id=uuid4(),
        paths=object(),
        artifact_catalog=EmptyCatalog(),
    )


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


class _FakeArtifactCatalog:
    """Records the workspace-id scoped enumerations the sweep reads."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def list_storage_refs(self, *, workspace_id: object) -> list[str]:
        """
        Enumerate storage references for a workspace.

        Parameters:
            workspace_id (object): Identifier of the workspace.

        Returns:
            list[str]: Storage references associated with the workspace.
        """
        self.calls.append(("storage", workspace_id))
        return ["ref-1"]

    async def list_completed_runs(self, *, workspace_id: object) -> list[CompletedRun]:
        """
        List completed runs for a workspace.

        Parameters:
                workspace_id (object): Identifier of the workspace.

        Returns:
                list[CompletedRun]: The workspace's completed runs.
        """
        self.calls.append(("completed", workspace_id))
        return []

    async def list_active_runs(self, *, workspace_id: object) -> list[CompletedRun]:
        """List active runs for a workspace.

        Parameters:
                workspace_id (object): Identifier of the workspace.

        Returns:
                list[CompletedRun]: Active runs associated with the workspace.
        """
        self.calls.append(("active", workspace_id))
        return []


@pytest.mark.asyncio
async def test_run_deferred_orphan_cleanup_enumerates_and_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.composition.daytona import run_deferred_orphan_cleanup

    workspace_id = uuid4()
    catalog = _FakeArtifactCatalog()

    async def fake_cleanup(
        gateway: object,
        *,
        workspace_id: object,
        paths: object,  # noqa: ARG001 - fake mirrors the real cleanup_orphan_bytes signature
        committed_storage_refs: object,
        completed_runs: object,  # noqa: ARG001 - fake mirrors the real cleanup_orphan_bytes signature
        active_runs: object,  # noqa: ARG001 - fake mirrors the real cleanup_orphan_bytes signature
        grace_period: object,  # noqa: ARG001 - fake mirrors the real cleanup_orphan_bytes signature
    ) -> OrphanCleanupReport:
        assert committed_storage_refs == ["ref-1"]
        assert gateway == "gateway"
        assert workspace_id == workspace_id
        return OrphanCleanupReport(scanned=3, removed=1, retained=1, skipped_fresh=1)

    monkeypatch.setattr("fleet_rlm.composition.daytona_workspace.cleanup_orphan_bytes", fake_cleanup)

    await run_deferred_orphan_cleanup(
        "gateway",
        workspace_id=workspace_id,
        paths=object(),
        artifact_catalog=catalog,
    )

    assert [name for name, _ in catalog.calls] == ["storage", "completed", "active"]


@pytest.mark.asyncio
async def test_run_deferred_orphan_cleanup_swallows_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.composition import daytona as composition_module
    from fleet_rlm.composition.daytona import run_deferred_orphan_cleanup

    monkeypatch.setattr(composition_module, "_ORPHAN_CLEANUP_TIMEOUT_SECONDS", 0.05)

    async def hang(_gateway: object, **_kwargs: object) -> OrphanCleanupReport:
        await asyncio.sleep(1.0)
        raise AssertionError("cleanup timeout should have interrupted the sweep")

    monkeypatch.setattr("fleet_rlm.composition.daytona_workspace.cleanup_orphan_bytes", hang)

    # Must return (not raise) when the sweep exceeds its budget.
    await run_deferred_orphan_cleanup(
        "gateway",
        workspace_id=uuid4(),
        paths=object(),
        artifact_catalog=_FakeArtifactCatalog(),
    )


@pytest.mark.asyncio
async def test_run_deferred_orphan_cleanup_swallows_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.composition.daytona import run_deferred_orphan_cleanup

    async def boom(_gateway: object, **_kwargs: object) -> OrphanCleanupReport:
        raise RuntimeError("provider down")

    monkeypatch.setattr("fleet_rlm.composition.daytona_workspace.cleanup_orphan_bytes", boom)

    await run_deferred_orphan_cleanup(
        "gateway",
        workspace_id=uuid4(),
        paths=object(),
        artifact_catalog=_FakeArtifactCatalog(),
    )
