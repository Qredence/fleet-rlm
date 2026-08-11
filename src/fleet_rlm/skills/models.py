"""Immutable bundled Skill domain types."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from types import MappingProxyType
from uuid import UUID

import dspy

_SAFE_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SAFE_AFFORDANCE = re.compile(r"^[a-z0-9]+([._-][a-z0-9]+)*$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


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
            or version != version.strip()
            or not version.isprintable()
            or _SAFE_VERSION.fullmatch(version) is None
        ):
            raise ValueError("invalid expected skill version")


@dataclass(frozen=True, slots=True)
class SkillCard:
    """Bounded discovery metadata without instructions or resource bodies."""

    id: UUID
    name: str
    description: str
    version: str
    resources_available: bool
    affordances: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise ValueError("skill id must be a UUID")
        if not isinstance(self.name, str) or len(self.name) > 64 or _SAFE_NAME.fullmatch(self.name) is None:
            raise ValueError("invalid skill name")
        if (
            not isinstance(self.description, str)
            or not self.description.strip()
            or len(self.description) > 512
            or not self.description.isprintable()
        ):
            raise ValueError("skill description is required")
        if (
            not isinstance(self.version, str)
            or self.version != self.version.strip()
            or not self.version.isprintable()
            or _SAFE_VERSION.fullmatch(self.version) is None
        ):
            raise ValueError("invalid skill version")
        if not isinstance(self.resources_available, bool):
            raise ValueError("skill resource flag must be boolean")
        affordances = tuple(self.affordances)
        if (
            len(affordances) > 8
            or len(set(affordances)) != len(affordances)
            or any(
                not isinstance(value, str)
                or not 1 <= len(value) <= 32
                or not value.isprintable()
                or _SAFE_AFFORDANCE.fullmatch(value) is None
                for value in affordances
            )
        ):
            raise ValueError("invalid skill affordances")
        object.__setattr__(self, "affordances", affordances)


@dataclass(frozen=True, slots=True)
class SkillResource:
    """One explicitly declared UTF-8 Skill resource."""

    path: str
    media_type: str
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        path = PurePosixPath(self.path)
        if (
            not self.path
            or path.is_absolute()
            or ".." in path.parts
            or str(path) != self.path
            or self.path.startswith("./")
        ):
            raise ValueError("invalid skill resource path")
        if not self.media_type.strip():
            raise ValueError("skill resource media type is required")
        if not isinstance(self.content, str):
            raise TypeError("skill resource content must be UTF-8 text")


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """Trusted bundled Skill definition retained only by the host."""

    card: SkillCard
    instructions: str = field(repr=False)
    resources: Mapping[str, SkillResource] = field(default_factory=lambda: MappingProxyType({}), repr=False)
    signature: type[dspy.Signature] | None = None
    # Manifest-declared tools are capability guidance for the model. Runtime
    # authorization remains host-owned and is never derived from this list.
    allowed_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.instructions, str) or not self.instructions.strip():
            raise ValueError("skill instructions are required")
        values = dict(self.resources)
        if any(path != resource.path for path, resource in values.items()):
            raise ValueError("skill resource key does not match its path")
        object.__setattr__(self, "resources", MappingProxyType(values))
        object.__setattr__(self, "allowed_tools", tuple(self.allowed_tools))
        if self.card.resources_available != bool(values):
            raise ValueError("skill card resource flag does not match resources")


@dataclass(frozen=True, slots=True)
class ResolvedSkills:
    """Deterministic result of exact Skill selection."""

    cards: tuple[SkillCard, ...]
    selected: tuple[SkillDefinition, ...]
    instructions: tuple[str, ...]
    signature: type[dspy.Signature] | None
