"""Runtime volume browsing helpers used by the runtime router."""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from typing import NoReturn

from fastapi import HTTPException

from ..auth import NormalizedIdentity
from ..dependencies import ServerState
from ..routers.ws.helpers import _sanitize_id
from ..schemas.core import (
    VolumeFileContentResponse,
    VolumeProvider,
    VolumeTreeResponse,
)
from .common import VOLUME_OPERATION_TIMEOUT_SECONDS, run_blocking
from fleet_rlm.core.tools.modal_volumes import (
    list_volume_tree,
    read_volume_file_text,
)
from fleet_rlm.infrastructure.providers.daytona.volumes import (
    list_daytona_volume_tree,
    read_daytona_volume_file_text,
)


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


async def load_volume_tree(
    *,
    state: ServerState,
    identity: NormalizedIdentity,
    provider: VolumeProvider | None,
    root_path: str,
    max_depth: int,
) -> VolumeTreeResponse:
    effective_provider = resolve_volume_provider(state=state, provider=provider)

    if effective_provider == "daytona":
        daytona_volume_name = resolve_daytona_volume_name(
            identity=identity,
            state=state,
        )
        try:
            result = await run_blocking(
                list_daytona_volume_tree,
                daytona_volume_name,
                root_path,
                max_depth,
                timeout=VOLUME_OPERATION_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=504, detail="Volume listing timed out."
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Volume listing failed: {exc}",
            ) from exc

        return VolumeTreeResponse(provider=effective_provider, **result)
    try:
        result = await run_blocking(
            list_volume_tree,
            resolve_modal_volume_name(state=state),
            root_path,
            max_depth,
            timeout=VOLUME_OPERATION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504, detail="Volume listing timed out."
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Volume listing failed: {exc}",
        ) from exc

    return VolumeTreeResponse(provider=effective_provider, **result)


async def load_volume_file_content(
    *,
    state: ServerState,
    identity: NormalizedIdentity,
    provider: VolumeProvider | None,
    path: str,
    max_bytes: int,
) -> VolumeFileContentResponse:
    normalized_path = normalize_volume_file_path(path)
    effective_provider = resolve_volume_provider(state=state, provider=provider)

    if effective_provider == "daytona":
        daytona_volume_name = resolve_daytona_volume_name(
            identity=identity,
            state=state,
        )
        try:
            result = await run_blocking(
                read_daytona_volume_file_text,
                daytona_volume_name,
                normalized_path,
                max_bytes,
                timeout=VOLUME_OPERATION_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail="Volume file read timed out.",
            ) from exc
        except Exception as exc:
            raise_volume_file_error(exc)

        return VolumeFileContentResponse(provider=effective_provider, **result)
    try:
        result = await run_blocking(
            read_volume_file_text,
            resolve_modal_volume_name(state=state),
            normalized_path,
            max_bytes,
            timeout=VOLUME_OPERATION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="Volume file read timed out.",
        ) from exc
    except Exception as exc:
        raise_volume_file_error(exc)

    return VolumeFileContentResponse(provider=effective_provider, **result)
