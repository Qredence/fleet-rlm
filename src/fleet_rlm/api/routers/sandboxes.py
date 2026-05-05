"""Router for Daytona sandbox management."""

from __future__ import annotations

from typing import Annotated

from daytona import (
    DaytonaAuthenticationError,
    DaytonaAuthorizationError,
    DaytonaConnectionError,
    DaytonaNotFoundError,
    DaytonaTimeoutError,
)
from fastapi import APIRouter, HTTPException, Path, Query, status

from fleet_rlm.utils.sandbox_ownership import sandbox_owner_labels

from ..dependencies import ConfigDepsDep, HTTPIdentityDep
from ..runtime_services.sandboxes import (
    archive_sandbox,
    delete_sandbox,
    load_sandbox_detail,
    load_sandbox_list,
)
from ..schemas.sandbox import (
    SandboxArchiveResponse,
    SandboxDetailResponse,
    SandboxListResponse,
)
from ._types import OpenAPIResponses

_DAYTONA_NOT_FOUND_ERRORS: tuple[type[BaseException], ...] = (DaytonaNotFoundError,)
_DAYTONA_UNAVAILABLE_ERRORS: tuple[type[BaseException], ...] = (
    DaytonaConnectionError,
    DaytonaAuthenticationError,
    DaytonaAuthorizationError,
    DaytonaTimeoutError,
)

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
    return await load_sandbox_list(
        page=page,
        limit=limit,
        owner_labels=sandbox_owner_labels(
            tenant_claim=identity.tenant_claim,
            user_claim=identity.user_claim,
        ),
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
    try:
        return await load_sandbox_detail(
            sandbox_id=sandbox_id,
            owner_labels=sandbox_owner_labels(
                tenant_claim=identity.tenant_claim,
                user_claim=identity.user_claim,
            ),
            allow_unlabeled_legacy=_allow_unlabeled_legacy_sandboxes(config_deps),
        )
    except _DAYTONA_NOT_FOUND_ERRORS as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Sandbox not found: {exc}",
        ) from exc
    except _DAYTONA_UNAVAILABLE_ERRORS as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Sandbox service unavailable: {exc}",
        ) from exc


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
    try:
        await delete_sandbox(
            sandbox_id=sandbox_id,
            owner_labels=sandbox_owner_labels(
                tenant_claim=identity.tenant_claim,
                user_claim=identity.user_claim,
            ),
            allow_unlabeled_legacy=_allow_unlabeled_legacy_sandboxes(config_deps),
        )
    except _DAYTONA_NOT_FOUND_ERRORS as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Sandbox not found: {exc}",
        ) from exc
    except _DAYTONA_UNAVAILABLE_ERRORS as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Sandbox service unavailable: {exc}",
        ) from exc


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
    try:
        await archive_sandbox(
            sandbox_id=sandbox_id,
            owner_labels=sandbox_owner_labels(
                tenant_claim=identity.tenant_claim,
                user_claim=identity.user_claim,
            ),
            allow_unlabeled_legacy=_allow_unlabeled_legacy_sandboxes(config_deps),
        )
    except _DAYTONA_NOT_FOUND_ERRORS as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Sandbox not found: {exc}",
        ) from exc
    except _DAYTONA_UNAVAILABLE_ERRORS as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Sandbox service unavailable: {exc}",
        ) from exc
    return SandboxArchiveResponse()
