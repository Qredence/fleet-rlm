"""LLM provider profile and role binding routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query

from ..dependencies import ConfigDepsDep, DiagnosticsDepsDep, LmDepsDep, PersistenceDepsDep
from ..runtime_services import llm_profiles as llm_profile_service
from ..schemas.llm_profiles import (
    LlmImportEnvResponse,
    LlmModelCatalogResponse,
    LlmProviderProfileCreateRequest,
    LlmProviderProfileResponse,
    LlmProviderProfileUpdateRequest,
    LlmRoleBindingsResponse,
    LlmRoleBindingsUpdateRequest,
)
from ..schemas.runtime import RuntimeConnectivityTestResponse
from ._types import OpenAPIResponses

router = APIRouter(
    prefix="/runtime",
    tags=["runtime"],
)

AUTH_ERROR_RESPONSES: OpenAPIResponses = {
    401: {"description": "Authentication is required or the provided token is invalid."},
    503: {"description": "Runtime services are unavailable because server startup is incomplete."},
}

WRITE_RESPONSES: OpenAPIResponses = {
    **AUTH_ERROR_RESPONSES,
    403: {"description": "LLM profile writes are allowed only when APP_ENV=local."},
    404: {"description": "Requested profile was not found."},
}


@router.get(
    "/llm-profiles",
    response_model=list[LlmProviderProfileResponse],
    responses=AUTH_ERROR_RESPONSES,
)
async def list_llm_profiles(persistence_deps: PersistenceDepsDep) -> list[LlmProviderProfileResponse]:
    return await llm_profile_service.list_profiles(persistence_deps=persistence_deps)


@router.post(
    "/llm-profiles",
    response_model=LlmProviderProfileResponse,
    responses=WRITE_RESPONSES,
)
async def create_llm_profile(
    request: LlmProviderProfileCreateRequest,
    persistence_deps: PersistenceDepsDep,
    config_deps: ConfigDepsDep,
) -> LlmProviderProfileResponse:
    return await llm_profile_service.create_profile(
        persistence_deps=persistence_deps,
        config_deps=config_deps,
        request=request,
    )


@router.patch(
    "/llm-profiles/{profile_id}",
    response_model=LlmProviderProfileResponse,
    responses=WRITE_RESPONSES,
)
async def update_llm_profile(
    profile_id: Annotated[UUID, Path(description="Provider profile identifier.")],
    request: LlmProviderProfileUpdateRequest,
    persistence_deps: PersistenceDepsDep,
    config_deps: ConfigDepsDep,
) -> LlmProviderProfileResponse:
    return await llm_profile_service.update_profile(
        persistence_deps=persistence_deps,
        config_deps=config_deps,
        profile_id=profile_id,
        request=request,
    )


@router.delete(
    "/llm-profiles/{profile_id}",
    status_code=204,
    responses=WRITE_RESPONSES,
)
async def delete_llm_profile(
    profile_id: Annotated[UUID, Path(description="Provider profile identifier.")],
    persistence_deps: PersistenceDepsDep,
    config_deps: ConfigDepsDep,
) -> None:
    await llm_profile_service.delete_profile(
        persistence_deps=persistence_deps,
        config_deps=config_deps,
        profile_id=profile_id,
    )


@router.get(
    "/llm-profiles/{profile_id}/models",
    response_model=LlmModelCatalogResponse,
    responses={**AUTH_ERROR_RESPONSES, 404: {"description": "Requested profile was not found."}},
)
async def get_llm_profile_models(
    profile_id: Annotated[UUID, Path(description="Provider profile identifier.")],
    persistence_deps: PersistenceDepsDep,
    refresh: Annotated[bool, Query(description="Bypass cached model catalog results.")] = False,
) -> LlmModelCatalogResponse:
    return await llm_profile_service.get_model_catalog(
        persistence_deps=persistence_deps,
        profile_id=profile_id,
        force_refresh=refresh,
    )


@router.post(
    "/llm-profiles/{profile_id}/test",
    response_model=RuntimeConnectivityTestResponse,
    responses=WRITE_RESPONSES,
)
async def test_llm_profile(
    profile_id: Annotated[UUID, Path(description="Provider profile identifier.")],
    persistence_deps: PersistenceDepsDep,
    config_deps: ConfigDepsDep,
    diagnostics_deps: DiagnosticsDepsDep,
    lm_deps: LmDepsDep,
) -> RuntimeConnectivityTestResponse:
    return await llm_profile_service.test_profile_connection(
        persistence_deps=persistence_deps,
        config_deps=config_deps,
        diagnostics_deps=diagnostics_deps,
        lm_deps=lm_deps,
        profile_id=profile_id,
    )


@router.get(
    "/llm-roles",
    response_model=LlmRoleBindingsResponse,
    responses=AUTH_ERROR_RESPONSES,
)
async def get_llm_roles(persistence_deps: PersistenceDepsDep) -> LlmRoleBindingsResponse:
    return await llm_profile_service.get_role_bindings(persistence_deps=persistence_deps)


@router.patch(
    "/llm-roles",
    response_model=LlmRoleBindingsResponse,
    responses=WRITE_RESPONSES,
)
async def patch_llm_roles(
    request: LlmRoleBindingsUpdateRequest,
    persistence_deps: PersistenceDepsDep,
    config_deps: ConfigDepsDep,
    lm_deps: LmDepsDep,
    diagnostics_deps: DiagnosticsDepsDep,
) -> LlmRoleBindingsResponse:
    return await llm_profile_service.apply_role_bindings_patch(
        persistence_deps=persistence_deps,
        config_deps=config_deps,
        lm_deps=lm_deps,
        diagnostics_deps=diagnostics_deps,
        request=request,
    )


@router.post(
    "/llm-profiles/import-env",
    response_model=LlmImportEnvResponse,
    responses=WRITE_RESPONSES,
)
async def import_llm_profiles_from_env(
    persistence_deps: PersistenceDepsDep,
    config_deps: ConfigDepsDep,
    lm_deps: LmDepsDep,
    diagnostics_deps: DiagnosticsDepsDep,
) -> LlmImportEnvResponse:
    return await llm_profile_service.import_profile_from_env(
        persistence_deps=persistence_deps,
        config_deps=config_deps,
        lm_deps=lm_deps,
        diagnostics_deps=diagnostics_deps,
    )
