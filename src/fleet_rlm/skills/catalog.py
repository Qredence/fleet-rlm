"""Fixed trusted bundled Skill catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from types import MappingProxyType
from typing import Mapping
from uuid import UUID, uuid5

import dspy

from fleet_rlm.skills.errors import SkillNotFoundError
from fleet_rlm.skills.models import SkillCard, SkillDefinition, SkillResource
from fleet_rlm.skills.signatures import validate_skill_signature

_BUNDLED_SKILL_NAMESPACE = UUID("6f1e0c2a-9b3d-4e5f-8a1b-2c3d4e5f6071")


@dataclass(frozen=True, slots=True)
class _BundledSpec:
    name: str
    description: str
    version: str
    resources: tuple[tuple[str, str], ...] = ()
    signature: type[dspy.Signature] | None = None


_BUNDLED_SPECS = (
    _BundledSpec(
        "long-context",
        "Use bounded retrieval to analyze large documents, transcripts, code, or datasets.",
        "2.0.0",
        (
            ("scripts/semantic_chunk.py", "text/x-python"),
            ("scripts/rank_chunks.py", "text/x-python"),
            ("references/chunking-strategies.md", "text/markdown"),
        ),
    ),
    _BundledSpec(
        "workspace-files",
        "Use durable Session Workspace, Attachment, and Artifact tools correctly.",
        "1.0.0",
        (("references/filesystem-contract.md", "text/markdown"),),
    ),
)


def stable_skill_id(name: str) -> UUID:
    return uuid5(_BUNDLED_SKILL_NAMESPACE, name.strip())


@dataclass(frozen=True, slots=True, init=False)
class SkillCatalog:
    """Immutable Skill definitions keyed by stable UUID."""

    _definitions: Mapping[UUID, SkillDefinition] = field(repr=False)
    _cards: tuple[SkillCard, ...]

    def __init__(self, definitions: tuple[SkillDefinition, ...]) -> None:
        ordered = tuple(sorted(definitions, key=lambda item: (item.card.name, str(item.card.id))))
        values = {definition.card.id: definition for definition in ordered}
        if len(values) != len(ordered):
            raise ValueError("duplicate bundled Skill id")
        object.__setattr__(self, "_definitions", MappingProxyType(values))
        object.__setattr__(self, "_cards", tuple(definition.card for definition in ordered))

    def cards(self) -> tuple[SkillCard, ...]:
        return self._cards

    def get(self, skill_id: UUID) -> SkillDefinition | None:
        return self._definitions.get(skill_id)

    def require(self, skill_id: UUID) -> SkillDefinition:
        value = self.get(skill_id)
        if value is None:
            raise SkillNotFoundError("skill not found")
        return value


class UnavailableSkillCatalog(SkillCatalog):
    """Explicit private-test degradation fixture."""

    unavailable = True

    def __init__(self) -> None:
        super().__init__(())


def build_bundled_skill_catalog() -> SkillCatalog:
    root = files("fleet_rlm.skills").joinpath("bundled")
    definitions: list[SkillDefinition] = []
    for spec in _BUNDLED_SPECS:
        directory = root.joinpath(spec.name)
        instructions = directory.joinpath("SKILL.md").read_text(encoding="utf-8")
        resources = {
            path: SkillResource(path, media_type, directory.joinpath(path).read_text(encoding="utf-8"))
            for path, media_type in spec.resources
        }
        if spec.signature is not None:
            validate_skill_signature(spec.signature)
        card = SkillCard(stable_skill_id(spec.name), spec.name, spec.description, spec.version, bool(resources))
        definitions.append(SkillDefinition(card, instructions, resources, spec.signature))
    return SkillCatalog(tuple(definitions))
