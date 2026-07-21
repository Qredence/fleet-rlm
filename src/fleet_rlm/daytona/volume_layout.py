"""Acquire-time provisioning for the canonical Workspace Volume layout."""

from __future__ import annotations

from collections.abc import Iterable
from importlib.resources import files as resource_files
from importlib.resources.abc import Traversable
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from fleet_rlm.daytona.errors import DaytonaAdapterError, map_provider_error
from fleet_rlm.daytona.paths import VolumePaths, resolve_under_root

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
            paths.skills_root(),
            paths.memory_root(),
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
            paths.session_exports_dir(session_id),
            paths.session_staging_dir(session_id),
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
            paths.run_staging_dir(session_id, run_id),
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
    _ensure_skill_tree(fs, paths.skills_root())


def ensure_shared_volume_layout(sandbox: Any, paths: VolumePaths) -> None:
    """Ensure only the canonical shared roots for an existing mounted Volume."""
    fs = _sandbox_filesystem(sandbox)
    _require_directory(fs, str(paths.mount_path), create=False)
    _ensure_directories(fs, shared_volume_directories(paths))
    _ensure_skill_tree(fs, paths.skills_root())


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


def _ensure_skill_tree(fs: Any, destination_root: PurePosixPath) -> None:
    """Materialize the installed Skill package in the mounted Volume.

    The host-side Skill catalog remains the public registry, while this copy
    makes the same package resources available to Daytona code through the
    durable Workspace Volume.  Package resources are used instead of a host
    source path so this works from both a checkout and an installed wheel.
    """
    try:
        source_root = resource_files("fleet_rlm.skills")
        for relative_parts, data in _iter_resource_files(source_root):
            _ensure_skill_parent_directories(fs, destination_root, relative_parts[:-1])
            destination = str(resolve_under_root(destination_root, *relative_parts))
            existing = _file_info(fs, destination)
            if existing is not None and bool(getattr(existing, "is_dir", False)):
                raise DaytonaAdapterError(
                    message="Workspace Volume Skill asset conflicts with a directory",
                    cause_type="SkillTreeConflict",
                )
            try:
                fs.upload_file(data, destination)
            except Exception as exc:  # noqa: BLE001 - SDK exception types vary
                raise map_provider_error(exc) from exc
            uploaded = _file_info(fs, destination)
            if uploaded is None or bool(getattr(uploaded, "is_dir", False)):
                raise DaytonaAdapterError(
                    message="Workspace Volume Skill asset was not uploaded",
                    cause_type="SkillTreeUploadFailed",
                )
    except DaytonaAdapterError:
        raise
    except Exception as exc:  # noqa: BLE001 - package resource implementations vary
        raise DaytonaAdapterError(
            message="Bundled Skill resources are unavailable",
            cause_type="SkillTreeUnavailable",
        ) from exc


def _iter_resource_files(
    resource: Traversable,
    relative_parts: tuple[str, ...] = (),
) -> Iterable[tuple[tuple[str, ...], bytes]]:
    for child in sorted(resource.iterdir(), key=lambda item: item.name):
        name = child.name
        if name == "__pycache__" or name.endswith(".pyc"):
            continue
        if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            raise DaytonaAdapterError(
                message="Bundled Skill resource has an unsafe name",
                cause_type="SkillTreeUnsafePath",
            )
        child_parts = (*relative_parts, name)
        if child.is_dir():
            yield from _iter_resource_files(child, child_parts)
        elif child.is_file():
            try:
                yield child_parts, child.read_bytes()
            except Exception as exc:  # noqa: BLE001 - resource implementations vary
                raise DaytonaAdapterError(
                    message="Bundled Skill resource could not be read",
                    cause_type="SkillTreeUnavailable",
                ) from exc
        else:
            raise DaytonaAdapterError(
                message="Bundled Skill resource has an unsupported entry",
                cause_type="SkillTreeInvalid",
            )


def _ensure_skill_parent_directories(
    fs: Any,
    destination_root: PurePosixPath,
    relative_parts: tuple[str, ...],
) -> None:
    current = destination_root
    for part in relative_parts:
        current = resolve_under_root(current, part)
        _require_directory(fs, str(current), create=True)


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
