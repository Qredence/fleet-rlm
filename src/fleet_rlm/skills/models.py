"""Skill domain types for bounded progressive disclosure."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

SkillScope = Literal["system", "workspace"]
SkillTrust = Literal["system", "workspace", "untrusted"]
SkillVisibility = Literal["visible", "hidden"]
SkillResourceEncoding = Literal["utf-8", "base64"]


@dataclass(frozen=True, slots=True)
class SkillSelectionRef:
    """Version-pinned Skill selection persisted with a Turn input."""

    id: UUID
    expected_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise ValueError("skill selection id must be a UUID")
        version = self.expected_version
        if (
            not isinstance(version, str)
            or not version
            or len(version) > 64
            or version != version.strip()
            or not version.isprintable()
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", version) is None
        ):
            raise ValueError("invalid expected skill version")


@dataclass(frozen=True, slots=True)
class SkillResourceDescriptor:
    """Safe resource metadata disclosed with a loaded Skill."""

    path: str
    media_type: str
    byte_size: int
    encoding: SkillResourceEncoding


@dataclass(frozen=True, slots=True)
class SkillResource:
    """Host-only immutable resource bytes plus their bounded descriptor."""

    descriptor: SkillResourceDescriptor
    body: bytes = field(repr=False)

    @property
    def path(self) -> str:
        return self.descriptor.path


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
    capability_refs: tuple[str, ...] = field(default_factory=tuple)
    task_contract_ref: str | None = None


@dataclass(frozen=True, slots=True)
class SkillRecord:
    """Host-only Agent Skill definition (bodies stay off public cards/events)."""

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
    skill_markdown: str
    license: str | None = None
    compatibility: str | None = None
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    capability_refs: tuple[str, ...] = field(default_factory=tuple)
    task_contract_ref: str | None = None
    resources: tuple[SkillResource, ...] = field(default_factory=tuple, repr=False)

    def resource_map(self) -> dict[str, SkillResource]:
        return {resource.path: resource for resource in self.resources}

    def resource_manifest(self) -> tuple[SkillResourceDescriptor, ...]:
        return tuple(resource.descriptor for resource in self.resources)
