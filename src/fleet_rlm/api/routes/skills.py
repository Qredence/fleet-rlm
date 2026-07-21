"""Bounded bundled Skill Card discovery."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from fleet_rlm.api.schemas import SkillCardResponse
from fleet_rlm.skills.catalog import SkillCatalog
from fleet_rlm.skills.models import SkillCard

router = APIRouter(tags=["skills"])


def get_skill_catalog(request: Request) -> SkillCatalog:
    catalog = getattr(request.app.state, "skill_catalog", None)
    if not isinstance(catalog, SkillCatalog):
        raise RuntimeError("bundled Skill catalog is unavailable")
    return catalog


SkillCatalogDep = Annotated[SkillCatalog, Depends(get_skill_catalog)]


def _to_response(card: SkillCard) -> SkillCardResponse:
    return SkillCardResponse(
        id=card.id,
        name=card.name,
        description=card.description,
        scope="system",
        version=card.version,
        trust="system",
        affordances=[],
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
    q: Annotated[str | None, Query(description="Optional ranking query")] = None,
) -> list[SkillCardResponse]:
    """List SkillCards authorized for the caller (metadata only)."""
    return [_to_response(card) for card in _rank(catalog.cards(), q)]


@router.get("/api/skills/{skill_id}", response_model=SkillCardResponse, operation_id="get_skill")
def get_skill(skill_id: UUID, catalog: SkillCatalogDep) -> SkillCardResponse:
    skill = catalog.get(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return _to_response(skill.card)
