"""Runtime volume browsing helpers used by the runtime router."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, NoReturn

from fastapi import HTTPException

from fleet_rlm.integrations.providers.daytona.volumes import (
    list_daytona_volume_tree,
    read_daytona_volume_file_text,
)
from fleet_rlm.runtime.tools.modal_volumes import (
    list_volume_tree,
    read_volume_file_text,
)

from ..auth import NormalizedIdentity
from ..dependencies import ServerState
from ..schemas.core import (
    VolumeFileContentResponse,
    VolumeProvider,
    VolumeTreeResponse,
)
from ..server_utils import sanitize_id as _sanitize_id
from .common import VOLUME_OPERATION_TIMEOUT_SECONDS, run_blocking

VolumeOperation = Callable[[str, str, int], dict[str, Any]]


@dataclass(frozen=True)
class _ResolvedVolumeBackend:
    provider: VolumeProvider
    volume_name: str
    list_tree: VolumeOperation
    read_file_text: VolumeOperation


def resolve_daytona_volume_name(
    *, identity: NormalizedIdentity, state: ServerState
) -> str:
    """Return the workspace-scoped Daytona persistent volume name."""
    return _sanitize_id(identity.tenant_claim, state.config.ws_default_workspace_id)


def resolve_modal_volume_name(*, state: ServerState) -> str:
    volume_name = state.config.volume_name
    if not volume_name:
        raise HTTPException(
            status_code=422,
            detail="No Modal Volume configured (interpreter.volume_name is unset).",
        )
    return volume_name


def resolve_volume_provider(
    *,
    state: ServerState,
    provider: VolumeProvider | None,
) -> VolumeProvider:
    if provider is not None:
        return provider
    return "daytona" if state.config.sandbox_provider == "daytona" else "modal"


def normalize_volume_file_path(path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    if ".." in PurePosixPath(normalized_path).parts:
        raise HTTPException(status_code=400, detail="Invalid file path.")
    return normalized_path


def raise_volume_file_error(exc: Exception) -> NoReturn:
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
    state: ServerState,
    identity: NormalizedIdentity,
    provider: VolumeProvider | None,
) -> _ResolvedVolumeBackend:
    effective_provider = resolve_volume_provider(state=state, provider=provider)
    if effective_provider == "daytona":
        return _ResolvedVolumeBackend(
            provider=effective_provider,
            volume_name=resolve_daytona_volume_name(identity=identity, state=state),
            list_tree=list_daytona_volume_tree,
            read_file_text=read_daytona_volume_file_text,
        )
    return _ResolvedVolumeBackend(
        provider=effective_provider,
        volume_name=resolve_modal_volume_name(state=state),
        list_tree=list_volume_tree,
        read_file_text=read_volume_file_text,
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
        return await run_blocking(
            operation,
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
    state: ServerState,
    identity: NormalizedIdentity,
    provider: VolumeProvider | None,
    root_path: str,
    max_depth: int,
) -> VolumeTreeResponse:
    backend = _resolve_volume_backend(state=state, identity=identity, provider=provider)
    result = await _run_volume_operation(
        operation=backend.list_tree,
        volume_name=backend.volume_name,
        path=root_path,
        limit=max_depth,
        timeout_detail="Volume listing timed out.",
        error_prefix="Volume listing failed",
    )
    return VolumeTreeResponse(provider=backend.provider, **result)


async def load_volume_file_content(
    *,
    state: ServerState,
    identity: NormalizedIdentity,
    provider: VolumeProvider | None,
    path: str,
    max_bytes: int,
) -> VolumeFileContentResponse:
    normalized_path = normalize_volume_file_path(path)
    backend = _resolve_volume_backend(state=state, identity=identity, provider=provider)
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
