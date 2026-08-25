"""Read-only logical tree access for the LocalScope Workspace Volume."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Query

from fleet_rlm.api.dependencies import LocalScopeDep, SettingsDep, WorkspaceVolumeGatewayDep
from fleet_rlm.api.errors import http_error
from fleet_rlm.api.schemas import VolumeTreeResponse

router = APIRouter(prefix="/api/volume", tags=["volume"])


@router.get("/tree", response_model=VolumeTreeResponse, operation_id="list_volume_tree_api")
async def list_volume_tree(
    identity: LocalScopeDep,
    gateway: WorkspaceVolumeGatewayDep,
    settings: SettingsDep,
    root: Annotated[str, Query(min_length=1, max_length=256)] = ".",
    max_depth: Annotated[int, Query(ge=1, le=32)] = 8,
    max_files: Annotated[int, Query(ge=1, le=10_000)] = 2_000,
) -> VolumeTreeResponse:
    try:
        mount = PurePosixPath(settings.volume_mount_path)
        requested = PurePosixPath(root)
        if "\x00" in root or "\\" in root or ".." in requested.parts:
            raise ValueError("root escapes volume mount")
        logical_path = requested if requested.is_absolute() else mount.joinpath(*requested.parts)
        try:
            logical_path.relative_to(mount)
        except ValueError as exc:
            raise ValueError("root escapes volume mount") from exc
        logical_root = str(logical_path)
        fetched_files = await gateway.list_files(
            identity.workspace_id,
            logical_root,
            max_depth=max_depth,
            max_files=max_files + 1,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise http_error(400, "volume_tree_invalid", "Volume tree request is invalid") from exc
    except Exception as exc:
        raise http_error(503, "volume_unavailable", "Workspace Volume is unavailable") from exc
    truncated = len(fetched_files) > max_files
    relative_paths: set[str] = set()
    for file in fetched_files:
        if not isinstance(file.path, str) or "\x00" in file.path or "\\" in file.path:
            raise http_error(400, "volume_tree_invalid", "Volume tree request is invalid")
        path = PurePosixPath(file.path)
        if not path.is_absolute() or ".." in path.parts:
            raise http_error(400, "volume_tree_invalid", "Volume tree request is invalid")
        try:
            relative = path.relative_to(logical_path)
            relative_to_mount = path.relative_to(mount)
        except ValueError as exc:
            raise http_error(400, "volume_tree_invalid", "Volume tree request is invalid") from exc
        if not relative.parts or not relative_to_mount.parts:
            raise http_error(400, "volume_tree_invalid", "Volume tree request is invalid")
        relative_paths.add(str(relative_to_mount))
    paths = sorted(relative_paths)[:max_files]
    directories: list[str] = []
    if logical_path == mount:
        directories = ["artifacts", "attachments", "files", "projects", "sessions"]
    return VolumeTreeResponse(
        paths=paths,
        directories=directories,
        truncated=truncated,
    )
