"""Runtime settings, diagnostics, and volume browsing routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from ..bootstrap import get_delegate_lm_from_env, get_planner_lm_from_env
from fleet_rlm.utils.modal import load_modal_config

from ..dependencies import HTTPIdentityDep, ServerStateDep, require_http_identity
from ..runtime_services import (
    apply_runtime_settings_patch,
    build_runtime_settings_snapshot,
    build_runtime_status_response,
    json_model_response,
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


@router.get("/settings", response_model=RuntimeSettingsSnapshot)
async def get_runtime_settings(state: ServerStateDep) -> JSONResponse:
    return json_model_response(build_runtime_settings_snapshot(state=state))


@router.patch("/settings", response_model=RuntimeSettingsUpdateResponse)
async def patch_runtime_settings(
    state: ServerStateDep,
    request: RuntimeSettingsUpdateRequest,
) -> JSONResponse:
    return json_model_response(
        await apply_runtime_settings_patch(
            state=state,
            request=request,
            planner_loader=get_planner_lm_from_env,
            delegate_loader=get_delegate_lm_from_env,
        )
    )


@router.post("/tests/modal", response_model=RuntimeConnectivityTestResponse)
async def test_modal_connection(
    state: ServerStateDep,
) -> JSONResponse:
    return json_model_response(
        await run_modal_connection_test(
            state=state,
            load_modal_config=load_modal_config,
        )
    )


@router.post("/tests/lm", response_model=RuntimeConnectivityTestResponse)
async def test_lm_connection(state: ServerStateDep) -> JSONResponse:
    return json_model_response(
        await run_lm_connection_test(
            state=state,
            planner_loader=get_planner_lm_from_env,
            delegate_loader=get_delegate_lm_from_env,
        )
    )


@router.post("/tests/daytona", response_model=RuntimeConnectivityTestResponse)
async def test_daytona_connection(state: ServerStateDep) -> JSONResponse:
    return json_model_response(await run_daytona_connection_test(state=state))


@router.get("/status", response_model=RuntimeStatusResponse)
async def get_runtime_status(state: ServerStateDep) -> JSONResponse:
    return json_model_response(
        build_runtime_status_response(
            state=state,
            load_modal_config=load_modal_config,
        )
    )


@router.get("/volume/tree", response_model=VolumeTreeResponse)
async def get_volume_tree(
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    root_path: Annotated[str, Query()] = "/",
    max_depth: Annotated[int, Query(ge=1, le=10)] = 3,
    provider: Annotated[VolumeProvider | None, Query()] = None,
) -> JSONResponse:
    return json_model_response(
        await load_volume_tree(
            state=state,
            identity=identity,
            provider=provider,
            root_path=root_path,
            max_depth=max_depth,
        )
    )


@router.get("/volume/file", response_model=VolumeFileContentResponse)
async def get_volume_file_content(
    state: ServerStateDep,
    identity: HTTPIdentityDep,
    path: Annotated[str, Query(min_length=1)],
    max_bytes: Annotated[int, Query(ge=1, le=1_000_000)] = 200_000,
    provider: Annotated[VolumeProvider | None, Query()] = None,
) -> JSONResponse:
    return json_model_response(
        await load_volume_file_content(
            state=state,
            identity=identity,
            provider=provider,
            path=path,
            max_bytes=max_bytes,
        )
    )
