"""Ephemeral mounted-Sandbox gateway for independent Workspace file access."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from fleet_rlm.daytona.errors import map_provider_error
from fleet_rlm.daytona.provisioning import (
    DaytonaSandboxSpec,
    SandboxProvisioner,
    VolumeClient,
    VolumeConfig,
    ensure_shared_volume_layout,
    get_or_create_volume_id,
)
from fleet_rlm.daytona.workspace_fs import AsyncDaytonaSessionWorkspaceFS, AsyncDaytonaVolumeFS
from fleet_rlm.files.volume_paths import UnsafePathError, VolumePaths, validate_mount_path, validate_path_id
from fleet_rlm.files.volume_storage import VolumeFile, WorkspaceVolumeGateway, WorkspaceVolumeSession
from fleet_rlm.files.workspace_access import (
    WorkspaceFileConflictError,
    WorkspaceFileEntry,
    WorkspaceFileList,
    WorkspaceFileSession,
)
from fleet_rlm.files.workspace_models import WorkspaceEntry, WorkspaceTextPage
from fleet_rlm.persistence.repositories.artifacts import CompletedRun

logger = logging.getLogger(__name__)


def _public_entry(entry: WorkspaceEntry, checksum: str | None = None) -> WorkspaceFileEntry:
    return WorkspaceFileEntry(
        path=entry.path,
        kind=entry.kind,
        byte_size=entry.byte_size,
        modified_at=entry.modified_at,
        checksum_sha256=checksum,
    )


class _DaytonaWorkspaceFileSession:
    def __init__(self, workspace: AsyncDaytonaSessionWorkspaceFS, *, max_file_bytes: int) -> None:
        self._workspace = workspace
        self._max_file_bytes = max_file_bytes

    async def list_entries(
        self,
        path: str,
        *,
        limit: int,
        after: str | None,
    ) -> WorkspaceFileList:
        listing = await self._workspace.list_entries(path, limit=limit, after=after)
        return WorkspaceFileList(
            tuple(_public_entry(entry) for entry in listing.entries),
            listing.truncated,
            listing.next_cursor,
        )

    async def stat(self, path: str) -> WorkspaceFileEntry | None:
        entry = await self._workspace.stat(path)
        if entry is None:
            return None
        checksum = None
        if entry.kind == "file":
            content = await self._workspace.read_text(path, max_bytes=self._max_file_bytes)
            checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return _public_entry(entry, checksum)

    async def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
    ) -> WorkspaceTextPage:
        return await self._workspace.read_text_page(
            path,
            cursor=cursor,
            max_chars=max_chars,
            max_bytes=self._max_file_bytes,
        )

    async def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool,
        expected_sha256: str | None,
    ) -> WorkspaceFileEntry:
        await self._check_precondition(path, expected_sha256)
        entry = await self._workspace.write_text(
            path,
            content,
            overwrite=overwrite,
        )
        return _public_entry(entry, hashlib.sha256(content.encode("utf-8")).hexdigest())

    async def append_text(
        self,
        path: str,
        content: str,
        *,
        expected_sha256: str | None,
    ) -> WorkspaceFileEntry:
        current = await self._read_current(path)
        self._assert_precondition(current, expected_sha256)
        entry = await self._workspace.append_text(path, content)
        final = (current or "") + content
        return _public_entry(entry, hashlib.sha256(final.encode("utf-8")).hexdigest())

    async def _check_precondition(self, path: str, expected_sha256: str | None) -> None:
        if expected_sha256 is None:
            return
        self._assert_precondition(await self._read_current(path), expected_sha256)

    async def _read_current(self, path: str) -> str | None:
        entry = await self._workspace.stat(path)
        if entry is None:
            return None
        if entry.kind != "file":
            raise IsADirectoryError(path)
        return await self._workspace.read_text(path, max_bytes=self._max_file_bytes)

    @staticmethod
    def _assert_precondition(current: str | None, expected_sha256: str | None) -> None:
        if expected_sha256 is None:
            return
        actual = hashlib.sha256(current.encode("utf-8")).hexdigest() if current is not None else None
        if actual != expected_sha256:
            raise WorkspaceFileConflictError("Workspace file checksum precondition failed")


class _DaytonaWorkspaceVolumeSession:
    def __init__(self, sandbox: object, *, mount_path: str) -> None:
        self._files = AsyncDaytonaVolumeFS(sandbox)
        self._mount_path = validate_mount_path(mount_path)

    def _path(self, logical_path: str) -> str:
        path = PurePosixPath(logical_path)
        try:
            relative = path.relative_to(self._mount_path)
        except ValueError as exc:
            raise UnsafePathError("logical path escapes Workspace Volume Scope") from exc
        if not relative.parts or ".." in relative.parts:
            raise UnsafePathError("logical path escapes Workspace Volume Scope")
        return str(path)

    async def write_bytes(self, logical_path: str, data: bytes) -> None:
        await self._files.write_bytes(self._path(logical_path), data)

    async def read_bytes(self, logical_path: str) -> bytes:
        return await self._files.read_bytes(self._path(logical_path))

    async def remove_bytes(self, logical_path: str) -> None:
        await self._files.remove(self._path(logical_path))

    async def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
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
    """Create one purpose-labelled I/O Sandbox and mount one Workspace subpath."""

    def __init__(
        self,
        *,
        platform: Any,
        volume_client: VolumeClient,
        volume_config: VolumeConfig,
        sandbox_spec: DaytonaSandboxSpec,
        max_file_bytes: int,
    ) -> None:
        self._platform = platform
        self._volume_client = volume_client
        self._volume_config = volume_config
        self._sandbox_spec = sandbox_spec
        self._max_file_bytes = max_file_bytes
        self._workspace_locks: dict[UUID, asyncio.Lock] = {}
        self._provisioner = SandboxProvisioner(
            platform=platform,
            volume_config=volume_config,
            sandbox_spec=sandbox_spec,
        )

    @asynccontextmanager
    async def open_workspace(
        self,
        workspace_id: UUID,
        *,
        purpose: str,
    ) -> AsyncIterator[WorkspaceFileSession]:
        async with self.open_sandbox(workspace_id, purpose=purpose) as sandbox:
            paths = self._volume_config.paths()
            yield _DaytonaWorkspaceFileSession(
                AsyncDaytonaSessionWorkspaceFS(
                    sandbox,
                    volume_root=str(paths.mount_path),
                    root=str(paths.files_root()),
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
            sandbox: Any | None = None
            try:
                volume_id = await get_or_create_volume_id(
                    self._volume_client,
                    self._volume_config,
                )
                expected = self._provisioner.expected_mount(
                    volume_id=volume_id,
                    workspace_id=workspace_id,
                )
                sandbox = await self._provisioner.create(
                    expected,
                    labels={
                        "fleet-package": "fleet_rlm",
                        "purpose": purpose,
                        "workspace_id": str(workspace_id),
                    },
                    ephemeral=True,
                )
                await sandbox.refresh_data()
                self._provisioner.verify(sandbox, expected)
                paths = self._volume_config.paths()
                await ensure_shared_volume_layout(sandbox, paths)
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
                raise map_provider_error(exc) from exc
            finally:
                if sandbox is not None:
                    try:
                        await asyncio.shield(self._platform.delete(sandbox))
                    except Exception as exc:
                        logger.warning(
                            "Workspace I/O Sandbox deletion failed",
                            extra={
                                "workspace_id": str(workspace_id),
                                "error_type": type(exc).__name__,
                            },
                        )


class DaytonaWorkspaceVolumeGateway:
    """Byte adapter over the same mounted-Sandbox gateway used by public files."""

    def __init__(self, gateway: DaytonaWorkspaceGateway, *, mount_path: str) -> None:
        self._gateway = gateway
        self._mount_path = str(validate_mount_path(mount_path))

    @asynccontextmanager
    async def open_workspace(self, workspace_id: UUID) -> AsyncIterator[WorkspaceVolumeSession]:
        async with self._gateway.open_sandbox(
            workspace_id,
            purpose="workspace-volume-io",
        ) as sandbox:
            yield _DaytonaWorkspaceVolumeSession(
                sandbox,
                mount_path=self._mount_path,
            )

    async def write_bytes(self, workspace_id: UUID, logical_path: str, data: bytes) -> None:
        async with self.open_workspace(workspace_id) as volume:
            await volume.write_bytes(logical_path, data)

    async def read_bytes(self, workspace_id: UUID, logical_path: str) -> bytes:
        async with self.open_workspace(workspace_id) as volume:
            return await volume.read_bytes(logical_path)

    async def remove_bytes(self, workspace_id: UUID, logical_path: str) -> None:
        async with self.open_workspace(workspace_id) as volume:
            await volume.remove_bytes(logical_path)

    async def list_files(
        self,
        workspace_id: UUID,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]:
        async with self.open_workspace(workspace_id) as volume:
            return await volume.list_files(
                logical_root,
                max_depth=max_depth,
                max_files=max_files,
            )


@dataclass(frozen=True, slots=True)
class OrphanCleanupReport:
    scanned: int
    removed: int
    retained: int
    skipped_fresh: int


async def cleanup_orphan_bytes(
    gateway: WorkspaceVolumeGateway,
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
    if grace_period < timedelta(0):
        raise ValueError("grace_period must not be negative")
    if max_files <= 0:
        raise ValueError("max_files must be positive")
    cutoff = (now or datetime.now(UTC)).timestamp() - grace_period.total_seconds()
    async with gateway.open_workspace(workspace_id) as volume:
        artifact_files = await volume.list_files(str(paths.artifacts_root()), max_depth=2, max_files=max_files)
        snapshot_files = await volume.list_files(str(paths.sessions_root()), max_depth=6, max_files=max_files)
        scanned = removed = retained = skipped_fresh = 0
        for item in (*artifact_files, *snapshot_files):
            scanned += 1
            if item.modified_at > cutoff:
                skipped_fresh += 1
                continue
            if (
                _is_committed_artifact(item.path, paths, committed_storage_refs)
                or _is_active_run_file(item.path, paths, active_runs)
                or _is_completed_snapshot(item.path, paths, completed_runs)
            ):
                retained += 1
                continue
            if _is_artifact_candidate(item.path, paths) or _is_run_scoped_file(item.path, paths):
                await volume.remove_bytes(item.path)
                removed += 1
            else:
                retained += 1
    return OrphanCleanupReport(scanned, removed, retained, skipped_fresh)


def _is_artifact_candidate(path: str, paths: VolumePaths) -> bool:
    try:
        relative = PurePosixPath(path).relative_to(paths.artifacts_root())
    except ValueError:
        return False
    return len(relative.parts) == 2 and relative.parts[1] == "blob" and _is_uuid(relative.parts[0])


def _is_committed_artifact(path: str, paths: VolumePaths, keep: Collection[str]) -> bool:
    return _is_artifact_candidate(path, paths) and path in keep


def _is_snapshot_candidate(path: str, paths: VolumePaths) -> bool:
    try:
        relative = PurePosixPath(path).relative_to(paths.sessions_root())
    except ValueError:
        return False
    return (
        len(relative.parts) == 4
        and relative.parts[1] == "runs"
        and relative.parts[3] == "result.json"
        and _is_uuid(relative.parts[0])
        and _is_uuid(relative.parts[2])
    )


def _is_completed_snapshot(path: str, paths: VolumePaths, keep: Collection[CompletedRun]) -> bool:
    if not _is_snapshot_candidate(path, paths):
        return False
    relative = PurePosixPath(path).relative_to(paths.sessions_root())
    return CompletedRun(session_id=UUID(relative.parts[0]), run_id=UUID(relative.parts[2])) in keep


def _is_active_run_file(path: str, paths: VolumePaths, keep: Collection[CompletedRun]) -> bool:
    identity = _run_identity(path, paths)
    return identity is not None and identity in keep


def _is_run_scoped_file(path: str, paths: VolumePaths) -> bool:
    return _run_identity(path, paths) is not None


def _run_identity(path: str, paths: VolumePaths) -> CompletedRun | None:
    try:
        relative = PurePosixPath(path).relative_to(paths.sessions_root())
    except ValueError:
        return None
    if len(relative.parts) < 4 or relative.parts[1] != "runs":
        return None
    session_id, run_id = relative.parts[0], relative.parts[2]
    if not _is_uuid(session_id) or not _is_uuid(run_id):
        return None
    return CompletedRun(session_id=UUID(session_id), run_id=UUID(run_id))


def _is_uuid(value: str) -> bool:
    try:
        validate_path_id(value)
    except ValueError:
        return False
    return True


__all__ = [
    "DaytonaWorkspaceGateway",
    "DaytonaWorkspaceVolumeGateway",
    "OrphanCleanupReport",
    "cleanup_orphan_bytes",
]
