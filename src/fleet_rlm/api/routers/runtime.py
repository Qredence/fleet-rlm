"""Runtime settings, diagnostics, and volume browsing routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..bootstrap import get_delegate_lm_from_env, get_planner_lm_from_env
from fleet_rlm.utils.modal import load_modal_config

from ..dependencies import HTTPIdentityDep, ServerStateDep, require_http_identity
from ..runtime_services import (
    apply_runtime_settings_patch,
    build_runtime_settings_snapshot,
    build_runtime_status_response,
    load_volume_file_content,
    load_volume_tree,
    run_daytona_connection_test,
    run_lm_connection_test,
    run_modal_connection_test,
)
from ..schemas.core import (
    RuntimeConnectivityTestResponse,
    RuntimeSettingsSnapshot,
    RuntimeSettingsUpdateRequest,
    RuntimeSettingsUpdateResponse,
    RuntimeStatusResponse,
    VolumeProvider,
    VolumeFileContentResponse,
    VolumeTreeResponse,
)

router = APIRouter(
    prefix="/runtime",
    tags=["runtime"],
    dependencies=[Depends(require_http_identity)],
)

AUTH_ERROR_RESPONSES = {
    401: {
        "description": "Authentication is required or the provided token is invalid."
    },
    503: {
        "description": "Runtime services are unavailable because server startup is incomplete."
    },
}

SETTINGS_WRITE_RESPONSES = {
    **AUTH_ERROR_RESPONSES,
    400: {"description": "The supplied runtime setting values failed validation."},
    403: {"description": "Runtime settings can only be updated when APP_ENV=local."},
}

VOLUME_TREE_RESPONSES = {
    **AUTH_ERROR_RESPONSES,
    400: {"description": "The requested root path is invalid."},
    502: {
        "description": "The runtime volume provider failed to list the requested path."
    },
    504: {
        "description": "Volume listing timed out before the backend returned a result."
    },
}

VOLUME_FILE_RESPONSES = {
    **AUTH_ERROR_RESPONSES,
    400: {
        "description": "The requested file path is invalid or points to a directory."
    },
    404: {"description": "The requested runtime volume file does not exist."},
    502: {
        "description": "The runtime volume provider failed to read the requested file."
    },
    504: {
        "description": "Volume file reading timed out before the backend returned a result."
    },
}


@router.get(
    "/settings",
    response_model=RuntimeSettingsSnapshot,
    responses=AUTH_ERROR_RESPONSES,
)
async def get_runtime_settings(state: ServerStateDep) -> RuntimeSettingsSnapshot:
    """Return the effective runtime settings snapshot used by the local server."""
    return build_runtime_settings_snapshot(state=state)


@router.patch(
    "/settings",
    response_model=RuntimeSettingsUpdateResponse,
    responses=SETTINGS_WRITE_RESPONSES,
)
async def patch_runtime_settings(
    state: ServerStateDep,
    request: RuntimeSettingsUpdateRequest,
) -> RuntimeSettingsUpdateResponse:
    """Persist allowed runtime setting changes and hot-apply them in-process."""
    return await apply_runtime_settings_patch(
        state=state,
        request=request,
        planner_loader=get_planner_lm_from_env,
        delegate_loader=get_delegate_lm_from_env,
    )


@router.post(
    "/tests/modal",
    response_model=RuntimeConnectivityTestResponse,
    responses=AUTH_ERROR_RESPONSES,
)
async def test_modal_connection(
    state: ServerStateDep,
) -> RuntimeConnectivityTestResponse:
    """Run the Modal preflight and smoke test used by the Settings diagnostics UI."""
    return await run_modal_connection_test(
        state=state,
        load_modal_config=load_modal_config,
    )


@router.post(
    "/tests/lm",
    response_model=RuntimeConnectivityTestResponse,
    responses=AUTH_ERROR_RESPONSES,
)
async def test_lm_connection(state: ServerStateDep) -> RuntimeConnectivityTestResponse:
    """Verify that the planner and delegate language-model configuration can load."""
    return await run_lm_connection_test(
        state=state,
        planner_loader=get_planner_lm_from_env,
        delegate_loader=get_delegate_lm_from_env,
    )


@router.post(
    "/tests/daytona",
    response_model=RuntimeConnectivityTestResponse,
    responses=AUTH_ERROR_RESPONSES,
)
async def test_daytona_connection(
    state: ServerStateDep,
) -> RuntimeConnectivityTestResponse:
    """Run the Daytona preflight and connectivity check exposed in runtime diagnostics."""
    return await run_daytona_connection_test(state=state)


@router.get(
    "/status",
    response_model=RuntimeStatusResponse,
    responses=AUTH_ERROR_RESPONSES,
)
async def get_runtime_status(state: ServerStateDep) -> RuntimeStatusResponse:
    """Return the combined runtime readiness, model, and provider diagnostics snapshot."""
    return build_runtime_status_response(
        state=state,
        load_modal_config=load_modal_config,
    )


@router.get(
    "/volume/tree",
    response_model=VolumeTreeResponse,
    responses=VOLUME_TREE_RESPONSES,
)
async def get_volume_tree(
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    root_path: Annotated[
        str,
        Query(description="Directory path to list within the selected runtime volume."),
    ] = "/",
    max_depth: Annotated[
        int,
        Query(
            ge=1,
            le=10,
            description="Maximum directory depth to traverse while building the file tree.",
        ),
    ] = 3,
    provider: Annotated[
        VolumeProvider | None,
        Query(
            description="Optional runtime volume backend override. Defaults to the active sandbox provider."
        ),
    ] = None,
) -> VolumeTreeResponse:
    """List the runtime volume tree for the active workspace and provider."""
    return await load_volume_tree(
        state=state,
        identity=identity,
        provider=provider,
        root_path=root_path,
        max_depth=max_depth,
    )


@router.get(
    "/volume/file",
    response_model=VolumeFileContentResponse,
    responses=VOLUME_FILE_RESPONSES,
)
async def get_volume_file_content(
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    path: Annotated[
        str,
        Query(
            min_length=1,
            description="Absolute or volume-relative file path to preview from the runtime volume.",
        ),
    ],
    max_bytes: Annotated[
        int,
        Query(
            ge=1,
            le=1_000_000,
            description="Maximum number of bytes of text content to return in the preview response.",
        ),
    ] = 200_000,
    provider: Annotated[
        VolumeProvider | None,
        Query(
            description="Optional runtime volume backend override. Defaults to the active sandbox provider."
        ),
    ] = None,
) -> VolumeFileContentResponse:
    """Read a text preview for a single file from the runtime volume."""
    return await load_volume_file_content(
        state=state,
        identity=identity,
        provider=provider,
        path=path,
        max_bytes=max_bytes,
    )
