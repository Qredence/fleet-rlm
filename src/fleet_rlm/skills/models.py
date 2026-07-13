"""Skill domain types: public SkillCard vs host-only SkillRecord."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

SkillScope = Literal["system", "workspace"]
SkillTrust = Literal["system", "workspace", "untrusted"]
SkillVisibility = Literal["visible", "hidden"]


@dataclass(frozen=True, slots=True)
class SkillCard:
    """Bounded discovery metadata — never includes full instructions."""

    id: UUID
    name: str
    description: str
    scope: SkillScope
    version: str
    trust: SkillTrust
    affordances: tuple[str, ...]
    resources_available: bool


@dataclass(frozen=True, slots=True)
class SkillRecord:
    """Host-only skill definition (instructions stay off public cards)."""

    id: UUID
    name: str
    description: str
    scope: SkillScope
    version: str
    trust: SkillTrust
    visibility: SkillVisibility
    workspace_id: UUID | None
    affordances: tuple[str, ...]
    resources_available: bool
    instructions: str
    resources: tuple[str, ...] = field(default_factory=tuple)
    # Relative path -> body (host only; never on SkillCard)
    resource_bodies: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def resource_body_map(self) -> dict[str, str]:
        return dict(self.resource_bodies)
