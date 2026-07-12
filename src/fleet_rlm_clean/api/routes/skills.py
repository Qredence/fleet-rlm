"""GET /api/skills — authorized SkillCards only (no instruction bodies)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from fleet_rlm_clean.api.identity import RequestIdentity, get_request_identity
from fleet_rlm_clean.api.schemas import SkillCardResponse
from fleet_rlm_clean.skills.authorize import SkillAuthorizer
from fleet_rlm_clean.skills.errors import SkillNotFoundError
from fleet_rlm_clean.skills.models import SkillCard
from fleet_rlm_clean.skills.ranking import rank_authorized_cards
from fleet_rlm_clean.skills.registry import InMemorySkillRegistry

router = APIRouter(tags=["skills"])


def get_skill_registry(request: Request) -> InMemorySkillRegistry:
    registry = getattr(request.app.state, "skill_registry", None)
    if registry is not None:
        return registry
    registry = InMemorySkillRegistry()
    request.app.state.skill_registry = registry
    return registry


def get_skill_authorizer(
    request: Request,
    registry: Annotated[InMemorySkillRegistry, Depends(get_skill_registry)],
) -> SkillAuthorizer:
    authorizer = getattr(request.app.state, "skill_authorizer", None)
    if authorizer is not None:
        return authorizer
    authorizer = SkillAuthorizer(registry)
    request.app.state.skill_authorizer = authorizer
    return authorizer


def _to_response(card: SkillCard) -> SkillCardResponse:
    return SkillCardResponse(
        id=card.id,
        name=card.name,
        description=card.description,
        scope=card.scope,
        version=card.version,
        trust=card.trust,
        affordances=list(card.affordances),
        resources_available=card.resources_available,
    )


@router.get("/api/skills", response_model=list[SkillCardResponse])
async def list_skills(
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    authorizer: Annotated[SkillAuthorizer, Depends(get_skill_authorizer)],
    q: str | None = Query(default=None, description="Optional ranking query"),
) -> list[SkillCardResponse]:
    """List SkillCards authorized for the caller (metadata only)."""
    cards = authorizer.list_cards(
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    ranked = rank_authorized_cards(cards, q)
    return [_to_response(c) for c in ranked]


@router.get("/api/skills/{skill_id}", response_model=SkillCardResponse)
async def get_skill(
    skill_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    authorizer: Annotated[SkillAuthorizer, Depends(get_skill_authorizer)],
) -> SkillCardResponse:
    try:
        card = authorizer.authorize(
            skill_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
        )
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail="skill not found") from exc
    return _to_response(card)
