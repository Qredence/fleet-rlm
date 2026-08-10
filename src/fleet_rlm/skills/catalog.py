"""Fixed trusted bundled Skill catalog."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.resources import as_file, files
from types import MappingProxyType
from uuid import UUID, uuid5

import dspy

from fleet_rlm.skills.errors import SkillNotFoundError
from fleet_rlm.skills.manifest import SkillManifest, parse_bundled_skill_manifest
from fleet_rlm.skills.models import SkillCard, SkillDefinition, SkillResource
from fleet_rlm.skills.signatures import DataAnalysisSignature, validate_skill_signature

_BUNDLED_SKILL_NAMESPACE = UUID("6f1e0c2a-9b3d-4e5f-8a1b-2c3d4e5f6071")
# Host-only executable extension bindings. Discovery, identity, behavior
# metadata, and resources are owned by each validated SKILL.md manifest.
_SIGNATURE_BINDINGS: Mapping[str, type[dspy.Signature]] = MappingProxyType({"data-analysis": DataAnalysisSignature})


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


def load_bundled_skill_manifests() -> tuple[SkillManifest, ...]:
    """Parse all bundled manifests deterministically."""

    root = files("fleet_rlm.skills").joinpath("bundled")
    with as_file(root) as root_path:
        directories = tuple(
            sorted(
                (path for path in root_path.iterdir() if path.is_dir() and path.joinpath("SKILL.md").is_file()),
                key=lambda path: path.name,
            )
        )
        manifests = []
        for directory in directories:
            manifest = parse_bundled_skill_manifest(directory)
            if manifest.name != directory.name:
                raise ValueError(f"Skill manifest name does not match bundle directory: {directory.name}")
            manifests.append(manifest)
        return tuple(manifests)


def build_bundled_skill_catalog() -> SkillCatalog:
    manifests = load_bundled_skill_manifests()
    manifest_names = {manifest.name for manifest in manifests}
    unexpected_bindings = set(_SIGNATURE_BINDINGS) - manifest_names
    if unexpected_bindings:
        raise ValueError(f"host Signature bindings reference unknown Skills: {sorted(unexpected_bindings)}")
    definitions: list[SkillDefinition] = []
    for manifest in manifests:
        signature = _SIGNATURE_BINDINGS.get(manifest.name)
        if signature is not None:
            validate_skill_signature(signature)
        card = SkillCard(
            stable_skill_id(manifest.name),
            manifest.name,
            manifest.description,
            manifest.version,
            bool(manifest.resources),
            manifest.affordances,
        )
        resources = {
            resource.path: SkillResource(resource.path, resource.media_type, resource.content)
            for resource in manifest.resources
        }
        definitions.append(SkillDefinition(card, manifest.instructions, resources, signature))
    return SkillCatalog(tuple(definitions))


def bundled_skill_readme_diagnostics() -> tuple[str, ...]:
    """Verify the human catalog table was generated from canonical manifests."""

    root = files("fleet_rlm.skills").joinpath("bundled")
    with as_file(root) as root_path:
        readme = root_path.joinpath("README.md").read_text(encoding="utf-8")
    expected_table = "| Skill | Version | Description |\n|---|---:|---|\n" + "".join(
        f"| `{manifest.name}` | {manifest.version} | {manifest.description} |\n"
        for manifest in load_bundled_skill_manifests()
    ).rstrip("\n")
    if f"Fleet ships five runtime Skills:\n\n{expected_table}" not in readme:
        return (f"README table differs from canonical manifests:\n{expected_table}",)
    return ()
