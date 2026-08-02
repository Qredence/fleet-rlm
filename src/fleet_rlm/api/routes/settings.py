"""Loopback-only editor for the committed Fleet TOML policy."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from fleet_rlm.api.dependencies import ConfigPolicyDep, require_loopback_client
from fleet_rlm.api.schemas import SettingsPolicyPatchRequest, SettingsPolicyResponse
from fleet_rlm.config import FleetConfigurationError
from fleet_rlm.config_policy import PolicyAccessError, PolicyConflictError

router = APIRouter(tags=["settings"])


def _response(snapshot) -> SettingsPolicyResponse:
    return SettingsPolicyResponse(
        revision=snapshot.revision,
        active_profile=snapshot.active_profile,
        default_profile=snapshot.default_profile,
        available_profiles=list(snapshot.available_profiles),
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
        if body.profile is not None:
            return _response(policy.set_default_profile(body.profile, revision=body.revision))
        if body.scope is None or body.path is None or body.value is None:
            raise FleetConfigurationError("settings policy patch requires either profile or scope/path/value")
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
