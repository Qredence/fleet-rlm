"""Bounded bundled Skill Card discovery."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from fleet_rlm.api.dependencies import LocalScopeDep, SkillCatalogDep
from fleet_rlm.api.errors import http_error
from fleet_rlm.api.schemas import SkillCardResponse
from fleet_rlm.posthog_client import get_client, get_distinct_id
from fleet_rlm.skills.models import SkillCard

router = APIRouter(tags=["skills"])


def _to_response(card: SkillCard) -> SkillCardResponse:
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


def _rank(cards: tuple[SkillCard, ...], query: str | None) -> tuple[SkillCard, ...]:
    needle = (query or "").strip().lower()
    if not needle:
        return cards
    terms = tuple(dict.fromkeys(needle.split()))

    def key(card: SkillCard) -> tuple[int, str, str]:
        haystack = f"{card.name} {card.description}".lower()
        return (-sum(term in haystack for term in terms), card.name, str(card.id))

    return tuple(sorted(cards, key=key))


@router.get("/api/skills", response_model=list[SkillCardResponse], operation_id="list_skills")
def list_skills(
    catalog: SkillCatalogDep,
    identity: LocalScopeDep,
    q: Annotated[str | None, Query(description="Optional ranking query")] = None,
) -> list[SkillCardResponse]:
    """
    List skill metadata available to the caller, optionally ranked by a search query.
    
    Parameters:
    	q (str | None): Optional query used to rank skills by matching terms in their names or descriptions.
    
    Returns:
    	list[SkillCardResponse]: Response-formatted skill cards.
    """
    cards = _rank(catalog.cards(), q)
    ph = get_client()
    if ph is not None:
        ph.capture(
            distinct_id=get_distinct_id(),
            event="skill_listed",
            properties={
                "workspace_id": str(identity.workspace_id),
                "result_count": len(cards),
                "has_query": q is not None and q.strip() != "",
            },
        )
    return [_to_response(card) for card in cards]


@router.get("/api/skills/{skill_id}", response_model=SkillCardResponse, operation_id="get_skill")
def get_skill(skill_id: UUID, catalog: SkillCatalogDep) -> SkillCardResponse:
    skill = catalog.get(skill_id)
    if skill is None:
        raise http_error(404, "skill_not_found", "Skill not found")
    return _to_response(skill.card)
