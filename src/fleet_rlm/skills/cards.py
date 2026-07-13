"""Project host SkillRecord → public SkillCard (strip body)."""

from __future__ import annotations

from fleet_rlm.skills.models import SkillCard, SkillRecord


def to_card(record: SkillRecord) -> SkillCard:
    """Bounded card projection — never includes instructions or resources bodies."""
    return SkillCard(
        id=record.id,
        name=record.name,
        description=record.description,
        scope=record.scope,
        version=record.version,
        trust=record.trust,
        affordances=record.affordances,
        resources_available=record.resources_available,
    )
