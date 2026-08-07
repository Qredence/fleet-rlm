"""Loopback-only editor for the committed Fleet TOML policy."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from fleet_rlm.api.dependencies import ConfigPolicyDep, require_loopback_client
from fleet_rlm.api.errors import http_error
from fleet_rlm.api.schemas import SettingsPolicyPatchRequest, SettingsPolicyResponse
from fleet_rlm.config import FleetConfigurationError
from fleet_rlm.config_policy import PolicyAccessError, PolicyConflictError

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _response(snapshot) -> SettingsPolicyResponse:
    return SettingsPolicyResponse(
        revision=snapshot.revision,
        active_profile=snapshot.active_profile,
        default_profile=snapshot.default_profile,
        available_profiles=list(snapshot.available_profiles),
        scopes=list(snapshot.scopes),
    )


@router.get(
    "",
    response_model=SettingsPolicyResponse,
    operation_id="get_settings_policy",
    dependencies=[Depends(require_loopback_client)],
)
def get_settings_policy(policy: ConfigPolicyDep) -> SettingsPolicyResponse:
    try:
        return _response(policy.read())
    except (PolicyAccessError, FleetConfigurationError) as exc:
        raise http_error(503, "settings_unavailable", "Settings are unavailable") from exc


@router.patch(
    "",
    response_model=SettingsPolicyResponse,
    operation_id="update_settings_policy",
    dependencies=[Depends(require_loopback_client)],
)
def patch_settings_policy(body: SettingsPolicyPatchRequest, policy: ConfigPolicyDep) -> SettingsPolicyResponse:
    try:
        if body.profile is not None:
            return _response(policy.set_default_profile(body.profile, revision=body.revision))
        if body.scope is None or body.path is None or body.value is None:
            raise http_error(422, "settings_policy_invalid", "Settings value is invalid")
        return _response(policy.update(scope=body.scope, path=body.path, value=body.value, revision=body.revision))
    except PolicyConflictError as exc:
        raise http_error(409, "settings_revision_conflict", "Settings changed; reload before saving") from exc
    except (PolicyAccessError, FleetConfigurationError) as exc:
        raise http_error(422, "settings_policy_invalid", "Settings value is invalid") from exc
