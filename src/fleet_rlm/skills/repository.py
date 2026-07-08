"""Skill catalog repository — read-only views over bundled and volume skills."""

from __future__ import annotations

from fleet_rlm.skills.catalog import discover_scaffold_skills, iter_all_skill_metadata
from fleet_rlm.skills.permissions import is_skill_visible
from fleet_rlm.skills.schemas import SkillCatalogEntry, SkillRuntimeContext

AVAILABLE_SKILLS: dict[str, str] = discover_scaffold_skills()


def list_skill_names() -> list[str]:
    return sorted(AVAILABLE_SKILLS.keys())


def get_skill_description(name: str) -> str | None:
    return AVAILABLE_SKILLS.get(name)


def list_visible(context: SkillRuntimeContext) -> list[SkillCatalogEntry]:
    entries: list[SkillCatalogEntry] = []
    for metadata in iter_all_skill_metadata(context):
        if not is_skill_visible(metadata.name, metadata.scope, context.visibility):
            continue
        entries.append(
            SkillCatalogEntry(
                name=metadata.name,
                description=metadata.description,
                scope=metadata.scope,
                trust_level=metadata.trust_level,
                source=metadata.source,
            )
        )
    return entries


__all__ = [
    "AVAILABLE_SKILLS",
    "get_skill_description",
    "list_skill_names",
    "list_visible",
]
