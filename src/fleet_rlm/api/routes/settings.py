"""Loopback-only editor for the committed Fleet TOML policy."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from fleet_rlm.api.dependencies import ConfigPolicyDep, require_loopback_client
from fleet_rlm.api.errors import http_error
from fleet_rlm.api.schemas import SettingsPolicyPatchRequest, SettingsPolicyResponse
from fleet_rlm.config.policy import PolicyAccessError, PolicyConflictError, PolicyMutation
from fleet_rlm.config.settings import FleetConfigurationError
from fleet_rlm.observability.posthog import get_client, get_distinct_id

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
    """
    Update the settings policy's default profile or one atomic batch of fields.

    Parameters:
        body (SettingsPolicyPatchRequest): The requested profile, legacy field, or
            batch update, including the expected revision.

    Returns:
        SettingsPolicyResponse: The updated settings policy.

    Raises:
        HTTPException: If the revision conflicts, the update is invalid, or the policy is unavailable.
    """
    try:
        if body.profile is not None:
            result = _response(policy.set_default_profile(body.profile, revision=body.revision))
            update_kind = "profile"
            properties = {"update_kind": update_kind}
        elif body.updates or body.default_profile is not None:
            result = _response(
                policy.apply(
                    updates=tuple(
                        PolicyMutation(
                            scope=update.scope,
                            path=update.path,
                            value=update.value,
                            unset=update.unset,
                        )
                        for update in body.updates
                    ),
                    default_profile=body.default_profile,
                    revision=body.revision,
                )
            )
            update_kind = "batch"
            properties = {"update_kind": update_kind, "update_count": len(body.updates)}
        else:
            if body.scope is None or body.path is None or body.value is None:
                raise http_error(422, "settings_policy_invalid", "Settings value is invalid")
            result = _response(
                policy.update(scope=body.scope, path=body.path, value=body.value, revision=body.revision)
            )
            update_kind = "field"
            properties = {"update_kind": update_kind, "scope": body.scope, "path": body.path}
        ph = get_client()
        if ph is not None:
            ph.capture(
                distinct_id=get_distinct_id(),
                event="settings_policy_updated",
                properties=properties,
            )
        return result
    except PolicyConflictError as exc:
        raise http_error(409, "settings_revision_conflict", "Settings changed; reload before saving") from exc
    except (PolicyAccessError, FleetConfigurationError) as exc:
        raise http_error(422, "settings_policy_invalid", "Settings value is invalid") from exc
