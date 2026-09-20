"""System health, configuration policy, and bundled skill discovery routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from fleet_rlm import __version__
from fleet_rlm.api.dependencies import (
    ConfigPolicyDep,
    LocalScopeDep,
    RuntimeInventoryIfReadyDep,
    SettingsDep,
    SkillCatalogDep,
    require_loopback_client,
)
from fleet_rlm.api.errors import http_error
from fleet_rlm.api.schemas import (
    HealthLivenessResponse,
    HealthReadinessResponse,
    SettingsPolicyPatchRequest,
    SettingsPolicyResponse,
    SkillCardResponse,
)
from fleet_rlm.config.policy import PolicyAccessError, PolicyConflictError, PolicyMutation
from fleet_rlm.config.settings import FleetConfigurationError
from fleet_rlm.observability.posthog import capture
from fleet_rlm.skills.models import SkillCard

# ---------------------------------------------------------------------------
# Health Probes Router (/health)
# ---------------------------------------------------------------------------

health_router = APIRouter(prefix="/health", tags=["health"])


@health_router.get(
    "",
    response_model=HealthLivenessResponse,
    operation_id="get_health",
)
def get_health(settings: SettingsDep) -> HealthLivenessResponse:
    """Liveness: the process is serving HTTP regardless of composition state."""
    return HealthLivenessResponse(status="ok", app=settings.app_name, version=__version__)


@health_router.get(
    "/ready",
    response_model=HealthReadinessResponse,
    operation_id="get_readiness",
    responses={503: {"description": "Service is not ready"}},
)
async def get_readiness(inventory: RuntimeInventoryIfReadyDep) -> HealthReadinessResponse:
    """Readiness: composition installed and the configured database answers."""
    if inventory is None:
        raise http_error(503, "service_not_ready", "Service is not ready")
    database = await inventory.database.readiness()
    if database == "unreachable":
        raise http_error(503, "service_not_ready", "Service is not ready")
    if database == "ok":
        return HealthReadinessResponse(status="ready", database="ok")
    return HealthReadinessResponse(status="ready", database="not_configured")


# ---------------------------------------------------------------------------
# Settings Policy Router (/api/settings)
# ---------------------------------------------------------------------------

settings_router = APIRouter(prefix="/api/settings", tags=["settings"])


def _settings_response(snapshot) -> SettingsPolicyResponse:
    return SettingsPolicyResponse(
        revision=snapshot.revision,
        active_profile=snapshot.active_profile,
        default_profile=snapshot.default_profile,
        available_profiles=list(snapshot.available_profiles),
        scopes=list(snapshot.scopes),
    )


@settings_router.get(
    "",
    response_model=SettingsPolicyResponse,
    operation_id="get_settings_policy",
    dependencies=[Depends(require_loopback_client)],
    responses={503: {"description": "Settings are unavailable, or the service is not ready"}},
)
def get_settings_policy(policy: ConfigPolicyDep) -> SettingsPolicyResponse:
    try:
        return _settings_response(policy.read())
    except (PolicyAccessError, FleetConfigurationError) as exc:
        raise http_error(503, "settings_unavailable", "Settings are unavailable") from exc


@settings_router.patch(
    "",
    response_model=SettingsPolicyResponse,
    operation_id="update_settings_policy",
    dependencies=[Depends(require_loopback_client)],
    responses={
        409: {"description": "Settings changed; reload before saving"},
        422: {"description": "Settings value is invalid"},
        503: {"description": "Service is not ready"},
    },
)
def patch_settings_policy(body: SettingsPolicyPatchRequest, policy: ConfigPolicyDep) -> SettingsPolicyResponse:
    try:
        if body.profile is not None:
            result = _settings_response(policy.set_default_profile(body.profile, revision=body.revision))
            update_kind = "profile"
            properties = {"update_kind": update_kind}
        elif body.updates or body.default_profile is not None:
            result = _settings_response(
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
            result = _settings_response(
                policy.update(scope=body.scope, path=body.path, value=body.value, revision=body.revision)
            )
            update_kind = "field"
            properties = {"update_kind": update_kind, "scope": body.scope, "path": body.path}
        capture("settings_policy_updated", properties=properties)
        return result
    except PolicyConflictError as exc:
        raise http_error(409, "settings_revision_conflict", "Settings changed; reload before saving") from exc
    except (PolicyAccessError, FleetConfigurationError) as exc:
        raise http_error(422, "settings_policy_invalid", "Settings value is invalid") from exc


# ---------------------------------------------------------------------------
# Skills Discovery Router (tags: skills)
# ---------------------------------------------------------------------------

skills_router = APIRouter(tags=["skills"])


def _skill_card_response(card: SkillCard) -> SkillCardResponse:
    return SkillCardResponse(
        id=card.id,
        name=card.name,
        description=card.description,
        scope="system",
        version=card.version,
        trust="system",
        affordances=list(card.affordances),
        resources_available=card.resources_available,
    )


def _rank_skills(cards: tuple[SkillCard, ...], query: str | None) -> tuple[SkillCard, ...]:
    needle = (query or "").strip().lower()
    if not needle:
        return cards
    terms = tuple(dict.fromkeys(needle.split()))

    def key(card: SkillCard) -> tuple[int, str, str]:
        haystack = f"{card.name} {card.description}".lower()
        return (-sum(term in haystack for term in terms), card.name, str(card.id))

    return tuple(sorted(cards, key=key))


@skills_router.get(
    "/api/skills",
    response_model=list[SkillCardResponse],
    operation_id="list_skills",
    responses={503: {"description": "Service is not ready"}},
)
def list_skills(
    catalog: SkillCatalogDep,
    identity: LocalScopeDep,
    q: Annotated[str | None, Query(description="Optional ranking query")] = None,
) -> list[SkillCardResponse]:
    cards = _rank_skills(catalog.cards(), q)
    capture(
        "skill_listed",
        properties={
            "workspace_id": str(identity.workspace_id),
            "result_count": len(cards),
            "has_query": q is not None and q.strip() != "",
        },
    )
    return [_skill_card_response(card) for card in cards]


@skills_router.get(
    "/api/skills/{skill_id}",
    response_model=SkillCardResponse,
    operation_id="get_skill",
    responses={
        404: {"description": "Skill not found"},
        503: {"description": "Service is not ready"},
    },
)
def get_skill(skill_id: UUID, catalog: SkillCatalogDep) -> SkillCardResponse:
    skill = catalog.get(skill_id)
    if skill is None:
        raise http_error(404, "skill_not_found", "Skill not found")
    return _skill_card_response(skill.card)


router = health_router

__all__ = [
    "get_health",
    "get_readiness",
    "get_settings_policy",
    "get_skill",
    "health_router",
    "list_skills",
    "patch_settings_policy",
    "router",
    "settings_router",
    "skills_router",
]
