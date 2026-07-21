"""Bounded crash-recovery cleanup for Daytona Volume bytes."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from uuid import UUID

from fleet_rlm.daytona.paths import VolumePaths, validate_path_id
from fleet_rlm.daytona.workspace_volume import WorkspaceVolumeGateway
from fleet_rlm.persistence.repositories.artifacts import CompletedRun


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
    """Remove only stale, canonical bytes absent from the supplied keep sets.

    The caller supplies database-derived keep sets so this function has no
    unscoped delete capability. Enumeration is rooted at the two fixed Volume
    layout directories and capped independently for each root.
    """
    if grace_period < timedelta(0):
        raise ValueError("grace_period must not be negative")
    if max_files <= 0:
        raise ValueError("max_files must be positive")
    current = (now or datetime.now(timezone.utc)).timestamp()
    cutoff = current - grace_period.total_seconds()

    kept_storage_refs = frozenset(committed_storage_refs)
    kept_completed_runs = frozenset(completed_runs)
    kept_active_runs = frozenset(active_runs)
    artifact_files = await gateway.list_files(
        workspace_id,
        str(paths.artifacts_root()),
        max_depth=2,
        max_files=max_files,
    )
    snapshot_files = await gateway.list_files(
        workspace_id,
        str(paths.sessions_root()),
        max_depth=6,
        max_files=max_files,
    )

    scanned = removed = retained = skipped_fresh = 0
    for item in (*artifact_files, *snapshot_files):
        scanned += 1
        if item.modified_at > cutoff:
            skipped_fresh += 1
            continue
        if (
            _is_committed_artifact(item.path, paths, kept_storage_refs)
            or _is_active_run_file(item.path, paths, kept_active_runs)
            or _is_completed_snapshot(item.path, paths, kept_completed_runs)
        ):
            retained += 1
            continue
        if _is_artifact_candidate(item.path, paths) or _is_run_scoped_file(item.path, paths):
            await gateway.remove_bytes(workspace_id, item.path)
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


def _is_completed_snapshot(
    path: str,
    paths: VolumePaths,
    keep: Collection[CompletedRun],
) -> bool:
    if not _is_snapshot_candidate(path, paths):
        return False
    relative = PurePosixPath(path).relative_to(paths.sessions_root())
    return (
        CompletedRun(
            session_id=UUID(relative.parts[0]),
            run_id=UUID(relative.parts[2]),
        )
        in keep
    )


def _is_active_run_file(
    path: str,
    paths: VolumePaths,
    keep: Collection[CompletedRun],
) -> bool:
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


__all__ = ["OrphanCleanupReport", "cleanup_orphan_bytes"]
