"""Acquire-time provisioning for the canonical Workspace Volume layout."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from fleet_rlm.daytona.errors import DaytonaAdapterError, map_provider_error
from fleet_rlm.daytona.paths import VolumePaths

_DIRECTORY_MODE = "700"


def required_volume_directories(
    paths: VolumePaths,
    *,
    session_id: UUID,
    run_id: UUID,
) -> tuple[str, ...]:
    """Return the complete parent-first directory contract for one Run."""
    return (
        *shared_volume_directories(paths),
        *session_volume_directories(paths, session_id=session_id),
        *run_volume_directories(paths, session_id=session_id, run_id=run_id),
    )


def shared_volume_directories(paths: VolumePaths) -> tuple[str, ...]:
    """Return the canonical Workspace-scoped shared roots."""
    return tuple(
        str(path)
        for path in (
            paths.artifacts_root(),
            paths.attachments_root(),
            paths.sessions_root(),
        )
    )


def session_volume_directories(paths: VolumePaths, *, session_id: UUID) -> tuple[str, ...]:
    """Return the canonical containers for one existing Session."""
    return tuple(
        str(path)
        for path in (
            paths.session_dir(session_id),
            paths.session_workspace_dir(session_id),
            paths.session_runs_dir(session_id),
        )
    )


def run_volume_directories(paths: VolumePaths, *, session_id: UUID, run_id: UUID) -> tuple[str, ...]:
    """Return the canonical containers for one existing Run."""
    return tuple(
        str(path)
        for path in (
            paths.run_dir(session_id, run_id),
            paths.run_artifacts_dir(session_id, run_id),
            paths.run_attachments_dir(session_id, run_id),
        )
    )


def ensure_volume_layout(
    sandbox: Any,
    paths: VolumePaths,
    *,
    session_id: UUID,
    run_id: UUID,
) -> None:
    """Ensure the mounted Volume and one Run's canonical containers exist."""
    fs = _sandbox_filesystem(sandbox)
    _require_directory(fs, str(paths.mount_path), create=False)
    _ensure_directories(
        fs,
        required_volume_directories(paths, session_id=session_id, run_id=run_id),
    )


def ensure_shared_volume_layout(sandbox: Any, paths: VolumePaths) -> None:
    """Ensure only the canonical shared roots for an existing mounted Volume."""
    fs = _sandbox_filesystem(sandbox)
    _require_directory(fs, str(paths.mount_path), create=False)
    _ensure_directories(fs, shared_volume_directories(paths))


def ensure_session_volume_layout(sandbox: Any, paths: VolumePaths, *, session_id: UUID) -> None:
    """Ensure the canonical containers for one existing Session."""
    fs = _sandbox_filesystem(sandbox)
    _require_directory(fs, str(paths.mount_path), create=False)
    _ensure_directories(fs, session_volume_directories(paths, session_id=session_id))


def ensure_run_volume_layout(
    sandbox: Any,
    paths: VolumePaths,
    *,
    session_id: UUID,
    run_id: UUID,
) -> None:
    """Ensure the canonical containers for one existing Run."""
    fs = _sandbox_filesystem(sandbox)
    _require_directory(fs, str(paths.mount_path), create=False)
    _ensure_directories(fs, run_volume_directories(paths, session_id=session_id, run_id=run_id))


def _sandbox_filesystem(sandbox: Any) -> Any:
    fs = getattr(sandbox, "fs", None)
    if fs is None:
        raise DaytonaAdapterError(
            message="Daytona Sandbox filesystem is unavailable",
            cause_type="VolumeLayoutUnavailable",
        )
    return fs


def _ensure_directories(fs: Any, directories: Iterable[str]) -> None:
    for directory in directories:
        _require_directory(fs, directory, create=True)


def _require_directory(fs: Any, path: str, *, create: bool) -> None:
    info = _file_info(fs, path)
    if info is not None:
        _assert_directory(info)
        return
    if not create:
        raise DaytonaAdapterError(
            message="Workspace Volume mount is unavailable",
            cause_type="VolumeLayoutMissingMount",
        )

    try:
        fs.create_folder(path, _DIRECTORY_MODE)
    except Exception as exc:  # noqa: BLE001 - concurrent creators may race
        info = _file_info(fs, path)
        if info is None:
            raise map_provider_error(exc) from exc
        _assert_directory(info)
        return

    info = _file_info(fs, path)
    if info is None:
        raise DaytonaAdapterError(
            message="Workspace Volume directory was not created",
            cause_type="VolumeLayoutCreateFailed",
        )
    _assert_directory(info)


def _file_info(fs: Any, path: str) -> Any | None:
    try:
        return fs.get_file_info(path)
    except Exception as exc:  # noqa: BLE001 - SDK not-found types vary
        if _is_not_found(exc):
            return None
        raise map_provider_error(exc) from exc


def _is_not_found(exc: BaseException) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    if getattr(exc, "status_code", None) == 404:
        return True
    response = getattr(exc, "response", None)
    return response is not None and getattr(response, "status_code", None) == 404


def _assert_directory(info: Any) -> None:
    if bool(getattr(info, "is_dir", False)):
        return
    raise DaytonaAdapterError(
        message="Workspace Volume layout conflicts with an existing file",
        cause_type="VolumeLayoutConflict",
    )
