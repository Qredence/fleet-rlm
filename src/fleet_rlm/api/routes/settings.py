"""Loopback-only editor for the committed Fleet TOML policy."""

from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Request

from fleet_rlm.api.dependencies import ConfigPolicyDep
from fleet_rlm.api.schemas import SettingsPolicyPatchRequest, SettingsPolicyResponse
from fleet_rlm.config import FleetConfigurationError
from fleet_rlm.config_policy import PolicyAccessError, PolicyConflictError

router = APIRouter(tags=["settings"])


def require_loopback_client(request: Request) -> None:
    """Keep filesystem policy administration local even on an unsafe API bind."""
    # Reject requests that carry proxy-forwarding headers: a local reverse proxy
    # connecting from 127.0.0.1 would make non-local clients appear loopback.
    if request.headers.get("x-forwarded-for") or request.headers.get("forwarded"):
        raise HTTPException(
            status_code=403,
            detail={"code": "settings_local_only", "message": "Settings are available only from the local machine"},
        )
    host = request.client.host if request.client is not None else ""
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise HTTPException(
            status_code=403,
            detail={"code": "settings_local_only", "message": "Settings are available only from the local machine"},
        )


def _response(snapshot) -> SettingsPolicyResponse:
    return SettingsPolicyResponse(
        revision=snapshot.revision,
        active_profile=snapshot.active_profile,
        scopes=list(snapshot.scopes),
    )


@router.get(
    "/api/settings",
    response_model=SettingsPolicyResponse,
    operation_id="get_settings_policy",
    dependencies=[Depends(require_loopback_client)],
)
def get_settings_policy(policy: ConfigPolicyDep) -> SettingsPolicyResponse:
    try:
        return _response(policy.read())
    except (PolicyAccessError, FleetConfigurationError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "settings_unavailable", "message": "Settings are unavailable"},
        ) from exc


@router.patch(
    "/api/settings",
    response_model=SettingsPolicyResponse,
    operation_id="update_settings_policy",
    dependencies=[Depends(require_loopback_client)],
)
def patch_settings_policy(body: SettingsPolicyPatchRequest, policy: ConfigPolicyDep) -> SettingsPolicyResponse:
    try:
        return _response(policy.update(scope=body.scope, path=body.path, value=body.value, revision=body.revision))
    except PolicyConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "settings_revision_conflict", "message": "Settings changed; reload before saving"},
        ) from exc
    except (PolicyAccessError, FleetConfigurationError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "settings_policy_invalid", "message": "Settings value is invalid"},
        ) from exc
