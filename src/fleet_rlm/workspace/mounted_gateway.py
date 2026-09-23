"""Mounted Workspace file and byte adapters over a runtime-owned I/O lease."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator, Callable, Collection, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from fleet_rlm.artifacts.models import CompletedRun
from fleet_rlm.workspace.models import WorkspaceEntry, WorkspaceTextPage
from fleet_rlm.workspace.paths import UnsafePathError, VolumePaths, validate_mount_path
from fleet_rlm.workspace.storage import (
    AgentAsyncStorageSession,
    AgentAsyncVolumeStorage,
    VolumeFile,
    WorkspaceVolumeSession,
)
from fleet_rlm.workspace.workspace import (
    WorkspaceFileConflictError,
    WorkspaceFileEntry,
    WorkspaceFileList,
    WorkspaceFileSession,
)


def _public_entry(entry: WorkspaceEntry, checksum: str | None = None) -> WorkspaceFileEntry:
    """
    Convert an internal workspace entry into a public file-entry model.

    Parameters:
        entry (WorkspaceEntry): Internal workspace entry to convert.
        checksum (str | None): Optional SHA-256 checksum for the entry.

    Returns:
        WorkspaceFileEntry: Public representation of the workspace entry.
    """
    return WorkspaceFileEntry(
        path=entry.path,
        kind=entry.kind,
        byte_size=entry.byte_size,
        modified_at=entry.modified_at,
        checksum_sha256=checksum,
    )


class _DaytonaWorkspaceFileSession:
    def __init__(self, workspace: AgentAsyncStorageSession, *, max_file_bytes: int) -> None:
        self._workspace = workspace
        self._max_file_bytes = max_file_bytes

    @property
    def last_warnings(self) -> tuple[Mapping[str, object], ...]:
        return self._workspace.warnings()

    def warnings(self) -> tuple[Mapping[str, object], ...]:
        return self._workspace.warnings()

    async def stat_path(self, path: str, *, include_checksum: bool | None = None) -> WorkspaceEntry:
        return await self._workspace.stat_path(path, include_checksum=include_checksum)

    async def read_text(
        self,
        path: str,
        *,
        cursor: str | None = None,
        max_chars: int = 64_000,
    ) -> WorkspaceTextPage:
        return await self._workspace.read_text(path, cursor=cursor, max_chars=max_chars)

    async def list_entries(
        self,
        path: str,
        *,
        limit: int = 100,
        after: str | None = None,
    ) -> WorkspaceFileList:
        listing = await self._workspace.list_entries(path, limit=limit, after=after)
        return WorkspaceFileList(
            tuple(_public_entry(entry) for entry in listing.entries),
            listing.truncated,
            listing.next_cursor,
        )

    async def stat(self, path: str, *, include_checksum: bool | None = False) -> WorkspaceFileEntry | None:
        entry = await self._workspace.stat(path, include_checksum=include_checksum)
        if entry is None:
            return None
        checksum = entry.checksum_sha256 if entry.kind == "file" else None
        return _public_entry(entry, checksum)

    async def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None = None,
        max_chars: int = 64_000,
        max_bytes: int | None = None,
    ) -> WorkspaceTextPage:
        return await self._workspace.read_text_page(
            path,
            cursor=cursor,
            max_chars=max_chars,
            max_bytes=self._max_file_bytes if max_bytes is None else max_bytes,
        )

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool = True,
        expected_sha256: str | None = None,
    ) -> WorkspaceFileEntry:
        # The provider-side agent compares and mutates in one mounted
        # operation; a host read would reopen the TOCTOU window across I/O
        # Sandboxes.
        entry = await self._workspace.write_text(
            path,
            content,
            overwrite=overwrite,
            expected_sha256=expected_sha256,
        )
        return _public_entry(
            entry,
            entry.checksum_sha256 or hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )

    async def append_text(
        self,
        path: str,
        content: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceFileEntry:
        entry = await self._workspace.append_text(
            path,
            content,
            expected_sha256=expected_sha256,
        )
        return _public_entry(
            entry,
            entry.checksum_sha256,
        )

    async def delete_path(
        self,
        path: str,
        *,
        expected_sha256: str | None = None,
    ) -> None:
        # The agent enforces the optional checksum precondition inside the
        # same mounted operation as the unlink/rmdir (one round trip, no
        # stat/read TOCTOU window across sandbox calls).
        await self._workspace.delete_path(path, expected_sha256=expected_sha256)

    async def patch_text(
        self,
        path: str,
        old: str,
        new: str,
        *,
        expected_sha256: str | None = None,
    ) -> WorkspaceFileEntry:
        # Same single-operation contract as delete; the patched-file entry
        # carries the agent-computed checksum of the exact bytes published.
        entry = await self._workspace.patch_text(path, old, new, expected_sha256=expected_sha256)
        return _public_entry(entry, entry.checksum_sha256)


class _DaytonaWorkspaceVolumeSession:
    def __init__(self, sandbox: object, *, mount_path: str) -> None:
        self._mount_path = validate_mount_path(mount_path)
        self._files = AgentAsyncVolumeStorage(sandbox, mount_path=str(self._mount_path))

    def _path(self, logical_path: str) -> str:
        path = PurePosixPath(logical_path)
        try:
            relative = path.relative_to(self._mount_path)
        except ValueError as exc:
            raise UnsafePathError("logical path escapes Workspace Volume Scope") from exc
        if not relative.parts or ".." in relative.parts:
            raise UnsafePathError("logical path escapes Workspace Volume Scope")
        return str(path)

    async def write_bytes(
        self,
        logical_path: str,
        data: bytes,
        *,
        max_bytes: int | None = None,
    ) -> None:
        await self._files.write_bytes(self._path(logical_path), data, max_bytes=max_bytes)

    async def read_bytes(
        self,
        logical_path: str,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        return await self._files.read_bytes(self._path(logical_path), max_bytes=max_bytes)

    async def exists(self, logical_path: str) -> bool:
        return await self._files.exists(self._path(logical_path))

    async def remove_bytes(self, logical_path: str) -> None:
        await self._files.remove_bytes(self._path(logical_path))

    async def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int = 10,
        max_files: int = 1000,
    ) -> tuple[VolumeFile, ...]:
        path = PurePosixPath(logical_root)
        # Listing the mount root is valid even though byte operations require
        # a concrete child path. This is the public `/api/volume/tree` default.
        target = str(self._mount_path) if path == self._mount_path else self._path(logical_root)
        return await self._files.list_files(
            target,
            max_depth=max_depth,
            max_files=max_files,
        )


class DaytonaWorkspaceGateway:
    """Adapt runtime-owned Workspace I/O Sandboxes to file operations."""

    def __init__(
        self,
        *,
        runtime: Any,
        paths: VolumePaths,
        max_file_bytes: int,
        map_error: Callable[[Exception], Exception],
    ) -> None:
        self._runtime = runtime
        self._paths = paths
        self._max_file_bytes = max_file_bytes
        self._map_error = map_error
        self._workspace_locks: dict[UUID, asyncio.Lock] = {}

    @asynccontextmanager
    async def open_workspace(
        self,
        workspace_id: UUID,
        *,
        purpose: str,
    ) -> AsyncIterator[WorkspaceFileSession]:
        async with self.open_sandbox(workspace_id, purpose=purpose) as sandbox:
            yield _DaytonaWorkspaceFileSession(
                AgentAsyncStorageSession(
                    sandbox,
                    volume_root=str(self._paths.mount_path),
                    root=str(self._paths.files_root()),
                    max_file_bytes=self._max_file_bytes,
                ),
                max_file_bytes=self._max_file_bytes,
            )

    @asynccontextmanager
    async def open_sandbox(
        self,
        workspace_id: UUID,
        *,
        purpose: str,
    ) -> AsyncIterator[Any]:
        """Yield one verified mounted Sandbox for a bounded I/O operation group."""
        lock = self._workspace_locks.setdefault(workspace_id, asyncio.Lock())
        async with lock:
            try:
                async with self._runtime.open_workspace_sandbox(workspace_id, purpose=purpose) as sandbox:
                    yield sandbox
            except (
                ValueError,
                WorkspaceFileConflictError,
                FileNotFoundError,
                FileExistsError,
                IsADirectoryError,
                NotADirectoryError,
            ):
                raise
            except Exception as exc:
                raise self._map_error(exc) from exc


class DaytonaWorkspaceVolumeGateway:
    """Byte adapter over the same mounted-Sandbox gateway used by public files."""

    def __init__(self, gateway: DaytonaWorkspaceGateway, *, mount_path: str) -> None:
        self._gateway = gateway
        self._mount_path = str(validate_mount_path(mount_path))

    @asynccontextmanager
    async def open_workspace(
        self,
        workspace_id: UUID,
        *,
        purpose: str | None = None,
    ) -> AsyncIterator[WorkspaceVolumeSession]:
        async with self._gateway.open_sandbox(
            workspace_id,
            purpose=purpose or "workspace-volume-io",
        ) as sandbox:
            yield _DaytonaWorkspaceVolumeSession(
                sandbox,
                mount_path=self._mount_path,
            )

    async def write_bytes(
        self,
        workspace_id: UUID,
        logical_path: str,
        data: bytes,
        *,
        max_bytes: int | None = None,
    ) -> None:
        async with self.open_workspace(workspace_id) as volume:
            await volume.write_bytes(logical_path, data, max_bytes=max_bytes)

    async def read_bytes(
        self,
        workspace_id: UUID,
        logical_path: str,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        async with self.open_workspace(workspace_id) as volume:
            return await volume.read_bytes(logical_path, max_bytes=max_bytes)

    async def remove_bytes(self, workspace_id: UUID, logical_path: str) -> None:
        async with self.open_workspace(workspace_id) as volume:
            await volume.remove_bytes(logical_path)

    async def list_files(
        self,
        workspace_id: UUID,
        logical_root: str,
        *,
        max_depth: int = 10,
        max_files: int = 1000,
    ) -> tuple[VolumeFile, ...]:
        async with self.open_workspace(workspace_id) as volume:
            return await volume.list_files(
                logical_root,
                max_depth=max_depth,
                max_files=max_files,
            )


# ---------------------------------------------------------------------------
# Orphan byte sweep (delegates to the canonical provider-neutral sweep)
# ---------------------------------------------------------------------------

from fleet_rlm.workspace.storage import OrphanCleanupReport  # noqa: E402
from fleet_rlm.workspace.storage import cleanup_orphan_bytes as _sweep_orphan_bytes  # noqa: E402


async def cleanup_orphan_bytes(
    gateway: DaytonaWorkspaceVolumeGateway,
    *,
    workspace_id: UUID,
    paths: VolumePaths,
    committed_storage_refs: Collection[str],
    completed_runs: Collection[CompletedRun],
    active_runs: Collection[CompletedRun] = (),
    now: datetime | None = None,
    grace_period: timedelta = timedelta(hours=1),
    max_files: int = 1024,
) -> OrphanCleanupReport:
    """Sweep orphan bytes through the canonical provider-neutral policy."""
    async with gateway.open_workspace(workspace_id) as volume:
        return await _sweep_orphan_bytes(
            volume,
            paths=paths,
            committed_storage_refs=committed_storage_refs,
            completed_runs=[(run.session_id, run.run_id) for run in completed_runs],
            active_runs=[(run.session_id, run.run_id) for run in active_runs],
            now=now,
            grace_period=grace_period,
            max_files=max_files,
        )


logger = logging.getLogger(__name__)
_ORPHAN_CLEANUP_TIMEOUT_SECONDS = 60


async def run_deferred_orphan_cleanup(
    gateway: Any,
    *,
    workspace_id: UUID,
    paths: Any,
    artifact_catalog: Any,
    grace_period: timedelta = timedelta(hours=1),
) -> None:
    """Best-effort orphan sweep that must never block startup readiness.

    Runs as a tracked background task after the composition is installed; its
    sandbox creation (cold provisioning) and deletions are deliberately kept off
    the readiness-critical path. Failures and timeouts are logged and left for a
    later startup.
    """
    from fleet_rlm.workspace.mounted_gateway import OrphanCleanupReport, cleanup_orphan_bytes

    committed_storage_refs = await artifact_catalog.list_storage_refs(workspace_id=workspace_id)
    completed_runs = await artifact_catalog.list_completed_runs(workspace_id=workspace_id)
    active_runs = await artifact_catalog.list_active_runs(workspace_id=workspace_id)
    if not committed_storage_refs and not completed_runs and not active_runs:
        # The fresh startup sweep has no durable candidates. Do not provision
        # an ephemeral sandbox just to discover an empty Volume; that extra
        # mount call races the first Turn's Volume creation/readiness work.
        cleanup_report = OrphanCleanupReport(scanned=0, removed=0, retained=0, skipped_fresh=0)
        logger.info(
            "Daytona orphan cleanup complete phase=orphan_cleanup scanned=0 removed=0 retained=0 "
            "skipped_fresh=0 deferred=true"
        )
        return
    try:
        async with asyncio.timeout(_ORPHAN_CLEANUP_TIMEOUT_SECONDS):
            cleanup_report = await cleanup_orphan_bytes(
                gateway,
                workspace_id=workspace_id,
                paths=paths,
                committed_storage_refs=committed_storage_refs,
                completed_runs=completed_runs,
                active_runs=active_runs,
                grace_period=grace_period,
            )
    except TimeoutError:
        logger.warning(
            "Daytona orphan cleanup timed out phase=orphan_cleanup timeout_seconds=%.3f; left for a later startup",
            _ORPHAN_CLEANUP_TIMEOUT_SECONDS,
        )
        return
    except Exception:
        logger.warning("Daytona orphan cleanup failed phase=orphan_cleanup", exc_info=True)
        return
    logger.info(
        "Daytona orphan cleanup complete phase=orphan_cleanup scanned=%d removed=%d retained=%d "
        "skipped_fresh=%d deferred=true",
        cleanup_report.scanned,
        cleanup_report.removed,
        cleanup_report.retained,
        cleanup_report.skipped_fresh,
    )


__all__ = [
    "DaytonaWorkspaceGateway",
    "DaytonaWorkspaceVolumeGateway",
    "OrphanCleanupReport",
    "cleanup_orphan_bytes",
    "run_deferred_orphan_cleanup",
]
