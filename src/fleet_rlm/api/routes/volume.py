"""Read-only logical tree access for the LocalScope Workspace Volume."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from fleet_rlm.api.dependencies import SettingsDep, WorkspaceVolumeGatewayDep
from fleet_rlm.api.local_scope import LocalScope, get_local_scope
from fleet_rlm.api.schemas import VolumeTreeResponse

router = APIRouter(prefix="/api/volume", tags=["volume"])


@router.get("/tree", response_model=VolumeTreeResponse, operation_id="list_volume_tree_api")
async def list_volume_tree(
    identity: Annotated[LocalScope, Depends(get_local_scope)],
    gateway: WorkspaceVolumeGatewayDep,
    settings: SettingsDep,
    root: str = Query(default=".", min_length=1, max_length=256),
    max_depth: int = Query(default=8, ge=1, le=32),
    max_files: int = Query(default=2_000, ge=1, le=10_000),
) -> VolumeTreeResponse:
    try:
        mount = PurePosixPath(settings.volume_mount_path)
        requested = PurePosixPath(root)
        logical_root = str(mount / requested) if not requested.is_absolute() else str(requested)
        if not logical_root.startswith(f"{mount}/") and logical_root != str(mount):
            raise ValueError("root escapes volume mount")
        fetched_files = await gateway.list_files(
            identity.workspace_id,
            logical_root,
            max_depth=max_depth,
            max_files=max_files + 1,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "volume_tree_invalid", "message": "Volume tree request is invalid"},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "volume_unavailable", "message": "Workspace Volume is unavailable"},
        ) from exc
    prefix = f"{mount}/"
    truncated = len(fetched_files) > max_files
    files = fetched_files[:max_files]
    paths = sorted({file.path.removeprefix(prefix) for file in files})
    directories: list[str] = []
    if requested == PurePosixPath("."):
        directories = ["artifacts", "attachments", "files", "sessions"]
    return VolumeTreeResponse(
        paths=paths,
        directories=directories,
        truncated=truncated,
    )
