"""Router for Daytona sandbox management."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from ..dependencies import ConfigDepsDep, HTTPIdentityDep
from ..runtime_services.sandbox_service import SandboxService
from ..schemas.sandbox import (
    SandboxArchiveResponse,
    SandboxDetailResponse,
    SandboxListResponse,
)
from ._types import OpenAPIResponses

router = APIRouter(
    prefix="/sandboxes",
    tags=["sandboxes"],
)


AUTH_ERROR_RESPONSES: OpenAPIResponses = {
    401: {
        "description": "Authentication is required or the provided token is invalid."
    },
    503: {
        "description": "Sandbox services are unavailable because server startup is incomplete."
    },
}


SBX_ERROR_RESPONSES: OpenAPIResponses = {
    401: {
        "description": "Authentication is required or the provided token is invalid."
    },
    404: {"description": "Sandbox not found or inaccessible."},
    503: {
        "description": "Sandbox services are unavailable because server startup is incomplete."
    },
}


def _allow_unlabeled_legacy_sandboxes(config_deps: ConfigDepsDep) -> bool:
    return (
        config_deps.config.app_env == "local" and config_deps.config.auth_mode == "dev"
    )


@router.get(
    "",
    response_model=SandboxListResponse,
    responses=AUTH_ERROR_RESPONSES,
    summary="List sandboxes",
    description="List active Daytona sandboxes with id, state, created_at, and volume info.",
)
async def list_sandboxes(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    page: Annotated[
        int,
        Query(ge=1, description="Page number for pagination (starting from 1)."),
    ] = 1,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=1000,
            description="Maximum number of sandboxes per page.",
        ),
    ] = 100,
) -> SandboxListResponse:
    """Return a paginated list of active Daytona sandboxes."""
    return await SandboxService().list_sandboxes(
        page=page,
        limit=limit,
        tenant_claim=identity.tenant_claim,
        user_claim=identity.user_claim,
        allow_unlabeled_legacy=_allow_unlabeled_legacy_sandboxes(config_deps),
    )


@router.get(
    "/{sandbox_id}",
    response_model=SandboxDetailResponse,
    responses=SBX_ERROR_RESPONSES,
    summary="Get sandbox details",
    description="Return full sandbox details including state, config, and volume.",
)
async def get_sandbox_detail(
    sandbox_id: Annotated[str, Path(description="Unique sandbox identifier.")],
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
) -> SandboxDetailResponse:
    """Return detailed information for a single Daytona sandbox."""
    return await SandboxService().get_sandbox_detail(
        sandbox_id=sandbox_id,
        tenant_claim=identity.tenant_claim,
        user_claim=identity.user_claim,
        allow_unlabeled_legacy=_allow_unlabeled_legacy_sandboxes(config_deps),
    )


@router.delete(
    "/{sandbox_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    responses=SBX_ERROR_RESPONSES,
    summary="Delete sandbox",
    description="Stop and permanently delete a Daytona sandbox.",
)
async def delete_sandbox_endpoint(
    sandbox_id: Annotated[str, Path(description="Unique sandbox identifier.")],
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
) -> None:
    """Stop and delete a Daytona sandbox."""
    await SandboxService().delete_sandbox(
        sandbox_id=sandbox_id,
        tenant_claim=identity.tenant_claim,
        user_claim=identity.user_claim,
        allow_unlabeled_legacy=_allow_unlabeled_legacy_sandboxes(config_deps),
    )


@router.post(
    "/{sandbox_id}/archive",
    response_model=SandboxArchiveResponse,
    responses=SBX_ERROR_RESPONSES,
    summary="Archive sandbox",
    description="Archive a Daytona sandbox to cold storage for later recovery.",
)
async def archive_sandbox_endpoint(
    sandbox_id: Annotated[str, Path(description="Unique sandbox identifier.")],
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
) -> SandboxArchiveResponse:
    """Archive a Daytona sandbox to cold storage."""
    return await SandboxService().archive_sandbox(
        sandbox_id=sandbox_id,
        tenant_claim=identity.tenant_claim,
        user_claim=identity.user_claim,
        allow_unlabeled_legacy=_allow_unlabeled_legacy_sandboxes(config_deps),
    )
