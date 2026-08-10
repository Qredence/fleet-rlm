"""Pure exact Skill selection."""

from __future__ import annotations

import dspy

from fleet_rlm.rlm.signature import FleetRLMSignature, root_signature_for_recursion
from fleet_rlm.skills.catalog import SkillCatalog
from fleet_rlm.skills.errors import InvalidSkillSelectionError
from fleet_rlm.skills.models import ResolvedSkills, SkillSelectionRef


def resolve_selected_skills(
    catalog: SkillCatalog,
    selections: tuple[SkillSelectionRef, ...],
    *,
    max_selections: int = 4,
) -> ResolvedSkills:
    values = tuple(selections)
    limit = int(max_selections)
    if not 0 <= limit <= 4:
        raise InvalidSkillSelectionError()
    if len(values) > limit or len({value.id for value in values}) != len(values):
        raise InvalidSkillSelectionError()
    selected = []
    signatures = []
    for selection in values:
        skill = catalog.get(selection.id)
        if skill is None or skill.card.version != selection.expected_version:
            raise InvalidSkillSelectionError()
        selected.append(skill)
        if skill.signature is not None:
            signatures.append(skill.signature)
    if len(signatures) > 1:
        raise InvalidSkillSelectionError()
    authorized_cards = tuple(skill.card for skill in selected)
    return ResolvedSkills(
        cards=catalog.cards() if not values else authorized_cards,
        selected=tuple(selected),
        instructions=tuple(skill.instructions for skill in selected),
        signature=signatures[0] if signatures else None,
    )


def resolved_signature(resolved: ResolvedSkills, *, recursion_enabled: bool = True) -> type[dspy.Signature]:
    """Return the active output Signature with Fleet operating instructions."""
    base = resolved.signature or FleetRLMSignature
    return root_signature_for_recursion(
        base,
        recursion_enabled=recursion_enabled,
        skill_instructions=resolved.instructions,
    )


def resolved_schema(resolved: ResolvedSkills) -> tuple[str, str]:
    if resolved.signature is None:
        return "fleet.default", "1"
    signed = next(skill for skill in resolved.selected if skill.signature is resolved.signature)
    return f"skill.{signed.card.name}", signed.card.version
