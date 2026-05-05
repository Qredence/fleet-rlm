"""Runtime volume browsing helpers used by the runtime router."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Awaitable, NoReturn, cast

from fastapi import HTTPException

from fleet_rlm.integrations.daytona.volume_runtime import (
    alist_daytona_volume_tree,
    alist_daytona_volumes,
    aread_daytona_volume_file_text,
)
from fleet_rlm.utils.identity import sanitize_id as _sanitize_id

from ..auth import NormalizedIdentity
from ..dependencies import ConfigDeps
from ..schemas.volumes import (
    VolumeFileContentResponse,
    VolumeListItem,
    VolumeListResponse,
    VolumeProvider,
    VolumeTreeResponse,
)
from .common import VOLUME_OPERATION_TIMEOUT_SECONDS, run_blocking

VolumeOperation = Callable[[str, str, int], dict[str, Any] | Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class _ResolvedVolumeBackend:
    provider: VolumeProvider
    volume_name: str
    list_tree: VolumeOperation
    read_file_text: VolumeOperation


def resolve_daytona_volume_name(
    *, identity: NormalizedIdentity, config_deps: ConfigDeps
) -> str:
    """Return the workspace-scoped Daytona persistent volume name."""
    return _sanitize_id(
        identity.tenant_claim, config_deps.config.ws_default_workspace_id
    )


def resolve_volume_provider(
    *,
    provider: VolumeProvider | None,
) -> VolumeProvider:
    """Select the effective volume backend, honoring request overrides first."""
    return provider or "daytona"


def normalize_volume_file_path(path: str) -> str:
    """Normalize a requested file path and reject traversal attempts."""
    normalized_path = path if path.startswith("/") else f"/{path}"
    if ".." in PurePosixPath(normalized_path).parts:
        raise HTTPException(status_code=400, detail="Invalid file path.")
    return normalized_path


def normalize_volume_tree_path(root_path: str) -> str:
    """Normalize a requested root path and reject traversal attempts."""
    normalized_path = root_path if root_path.startswith("/") else f"/{root_path}"
    normalized_path = normalized_path.rstrip("/") or "/"
    if ".." in PurePosixPath(normalized_path).parts:
        raise HTTPException(status_code=400, detail="Invalid root path.")
    return normalized_path


def normalize_volume_timestamp(value: Any) -> str | None:
    """Return a stable ISO-8601 timestamp string for provider metadata."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, datetime):
        created_at = (
            value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        )
        return created_at.isoformat()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        formatted = isoformat()
        if isinstance(formatted, str):
            return formatted
    return str(value)


def raise_volume_file_error(exc: Exception) -> NoReturn:
    """Convert provider-specific file read failures into stable HTTP errors."""
    message = str(exc).lower()
    if "no such file" in message or "not found" in message:
        raise HTTPException(status_code=404, detail="File not found.") from exc
    if "directory" in message:
        raise HTTPException(
            status_code=400, detail="Path must point to a file."
        ) from exc
    raise HTTPException(
        status_code=502, detail=f"Volume file read failed: {exc}"
    ) from exc


def _resolve_volume_backend(
    *,
    config_deps: ConfigDeps,
    identity: NormalizedIdentity,
    provider: VolumeProvider | None,
) -> _ResolvedVolumeBackend:
    effective_provider = resolve_volume_provider(provider=provider)
    effective_volume_name = resolve_daytona_volume_name(
        identity=identity, config_deps=config_deps
    )
    return _ResolvedVolumeBackend(
        provider=effective_provider,
        volume_name=effective_volume_name,
        list_tree=alist_daytona_volume_tree,
        read_file_text=aread_daytona_volume_file_text,
    )


async def _run_volume_operation(
    *,
    operation: VolumeOperation,
    volume_name: str,
    path: str,
    limit: int,
    timeout_detail: str,
    error_prefix: str,
    error_shaper: Callable[[Exception], NoReturn] | None = None,
) -> dict[str, Any]:
    try:
        result = operation(volume_name, path, limit)
        if inspect.isawaitable(result):
            return await asyncio.wait_for(
                result, timeout=VOLUME_OPERATION_TIMEOUT_SECONDS
            )
        sync_operation = cast(Callable[[str, str, int], dict[str, Any]], operation)
        return await run_blocking(
            sync_operation,
            volume_name,
            path,
            limit,
            timeout=VOLUME_OPERATION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail=timeout_detail) from exc
    except Exception as exc:
        if error_shaper is not None:
            error_shaper(exc)
        raise HTTPException(status_code=502, detail=f"{error_prefix}: {exc}") from exc


async def load_volume_tree(
    *,
    config_deps: ConfigDeps,
    identity: NormalizedIdentity,
    provider: VolumeProvider | None,
    root_path: str,
    max_depth: int,
) -> VolumeTreeResponse:
    """Load a normalized runtime volume tree for the selected provider."""
    normalized_root_path = normalize_volume_tree_path(root_path)
    backend = _resolve_volume_backend(
        config_deps=config_deps, identity=identity, provider=provider
    )
    result = await _run_volume_operation(
        operation=backend.list_tree,
        volume_name=backend.volume_name,
        path=normalized_root_path,
        limit=max_depth,
        timeout_detail="Volume listing timed out.",
        error_prefix="Volume listing failed",
    )
    return VolumeTreeResponse(provider=backend.provider, **result)


async def load_volume_file_content(
    *,
    config_deps: ConfigDeps,
    identity: NormalizedIdentity,
    provider: VolumeProvider | None,
    path: str,
    max_bytes: int,
) -> VolumeFileContentResponse:
    """Load a text preview for a normalized runtime volume file path."""
    normalized_path = normalize_volume_file_path(path)
    backend = _resolve_volume_backend(
        config_deps=config_deps, identity=identity, provider=provider
    )
    result = await _run_volume_operation(
        operation=backend.read_file_text,
        volume_name=backend.volume_name,
        path=normalized_path,
        limit=max_bytes,
        timeout_detail="Volume file read timed out.",
        error_prefix="Volume file read failed",
        error_shaper=raise_volume_file_error,
    )
    return VolumeFileContentResponse(provider=backend.provider, **result)


async def load_volume_list(
    *,
    config_deps: ConfigDeps,
    identity: NormalizedIdentity,
    provider: VolumeProvider | None,
) -> VolumeListResponse:
    """Return only the caller's active workspace volume for the selected provider."""
    effective_provider = resolve_volume_provider(provider=provider)
    if effective_provider != "daytona":
        raise HTTPException(status_code=400, detail="Unsupported volume provider.")

    try:
        volumes = await asyncio.wait_for(
            alist_daytona_volumes(),
            timeout=VOLUME_OPERATION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Volume list timed out.") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Volume list failed: {exc}"
        ) from exc

    workspace_volume_name = resolve_daytona_volume_name(
        identity=identity, config_deps=config_deps
    )
    workspace_volume = next(
        (volume for volume in volumes if volume.get("name") == workspace_volume_name),
        None,
    )
    if workspace_volume is None:
        workspace_volume = {
            "id": workspace_volume_name,
            "name": workspace_volume_name,
            "state": "unknown",
            "created_at": None,
        }
    created_at = workspace_volume.get("created_at")

    return VolumeListResponse(
        provider=effective_provider,
        volumes=[
            VolumeListItem(
                id=str(workspace_volume.get("id") or workspace_volume_name),
                name=str(workspace_volume.get("name") or workspace_volume_name),
                state=str(workspace_volume.get("state") or "unknown"),
                created_at=normalize_volume_timestamp(created_at),
            )
        ],
    )
