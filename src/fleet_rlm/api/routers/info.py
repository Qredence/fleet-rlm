"""Router for service information and feature-flag introspection."""

from __future__ import annotations

from fastapi import APIRouter

from ..dependencies import ConfigDepsDep, HTTPIdentityDep, LmDepsDep
from ..schemas.base import ServiceInfoResponse

router = APIRouter(prefix="/info", tags=["info"])


@router.get(
    "",
    response_model=ServiceInfoResponse,
    responses={
        401: {"description": "Authentication is required or the provided token is invalid."},
        503: {"description": "Service configuration is unavailable because server startup is incomplete."},
    },
    summary="Service information",
    description=(
        "Return a stable snapshot of build metadata and active feature flags "
        "for the running instance. Useful for operator introspection and client "
        "capability negotiation without tailing server logs."
    ),
)
def get_service_info(
    config_deps: ConfigDepsDep,
    lm_deps: LmDepsDep,
    identity: HTTPIdentityDep,
) -> ServiceInfoResponse:
    """Return build metadata and active feature flags for this instance."""
    cfg = config_deps.config
    return ServiceInfoResponse(
        app_env=cfg.app_env,
        auth_mode=cfg.auth_mode,
        auth_required=cfg.auth_required,
        sandbox_provider=cfg.sandbox_provider,
        database_enabled=cfg.database_url is not None,
        serve_ui=cfg.serve_ui,
        expose_docs=cfg.expose_docs,
        agent_model=cfg.agent_model,
        rlm_max_iterations=cfg.rlm_max_iterations,
    )
