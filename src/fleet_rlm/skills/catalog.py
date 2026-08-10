"""Fixed trusted bundled Skill catalog."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.resources import as_file, files
from types import MappingProxyType
from uuid import UUID, uuid5

import dspy

from fleet_rlm.skills.errors import SkillNotFoundError
from fleet_rlm.skills.models import SkillCard, SkillDefinition, SkillResource
from fleet_rlm.skills.signatures import DataAnalysisSignature, validate_skill_signature

_BUNDLED_SKILL_NAMESPACE = UUID("6f1e0c2a-9b3d-4e5f-8a1b-2c3d4e5f6071")


@dataclass(frozen=True, slots=True)
class _BundledSpec:
    name: str
    description: str
    version: str
    resources: tuple[tuple[str, str], ...] = ()
    signature: type[dspy.Signature] | None = None
    affordances: tuple[str, ...] = ()


_BUNDLED_SPECS = (
    _BundledSpec(
        "dspy-rlm",
        "Use when analyzing, explaining, or implementing dspy.RLM "
        "(Recursive Language Model / REPL code agent). Not for RAG or dspy.Retrieve.",
        "1.0.0",
        (("references/rlm-contract.md", "text/markdown"),),
        affordances=("interpreter", "llm_query"),
    ),
    _BundledSpec(
        "long-context",
        "Use bounded retrieval to analyze large documents, transcripts, code, or datasets.",
        "2.0.0",
        (
            ("scripts/semantic_chunk.py", "text/x-python"),
            ("scripts/rank_chunks.py", "text/x-python"),
            ("references/chunking-strategies.md", "text/markdown"),
        ),
        affordances=("fetch_url", "llm_query_batched", "workspace.files"),
    ),
    _BundledSpec(
        "workspace-files",
        "Use durable Session Workspace, Project, Attachment, and Artifact tools correctly.",
        "1.1.0",
        (("references/filesystem-contract.md", "text/markdown"),),
        affordances=("workspace.files", "artifacts.publish"),
    ),
    _BundledSpec(
        "data-analysis",
        "Compute and verify descriptive statistics, trends, and qualified anomalies.",
        "1.0.0",
        signature=DataAnalysisSignature,
        affordances=("artifacts.publish", "llm_query_batched"),
    ),
    _BundledSpec(
        "report-builder",
        "Create, save, read back, and verify reports from trusted source data.",
        "1.1.0",
        affordances=("workspace.files", "artifacts.publish"),
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
    from fleet_rlm.skills.manifest import parse_bundled_skill_manifest

    root = files("fleet_rlm.skills").joinpath("bundled")
    definitions: list[SkillDefinition] = []
    with as_file(root) as root_path:
        for spec in _BUNDLED_SPECS:
            directory = root_path.joinpath(spec.name)
            # Expand-phase authority boundary: parse/validate Skill-owned
            # metadata at build time, but keep the existing host declaration
            # as the runtime selection value so pinned Turn inputs do not
            # move in this ticket. Drift is compared by diagnostics below.
            parse_bundled_skill_manifest(directory)
            instructions = directory.joinpath("SKILL.md").read_text(encoding="utf-8")
            resources = {
                path: SkillResource(path, media_type, directory.joinpath(path).read_text(encoding="utf-8"))
                for path, media_type in spec.resources
            }
            if spec.signature is not None:
                validate_skill_signature(spec.signature)
            card = SkillCard(
                stable_skill_id(spec.name),
                spec.name,
                spec.description,
                spec.version,
                bool(resources),
                spec.affordances,
            )
            definitions.append(SkillDefinition(card, instructions, resources, spec.signature))
    return SkillCatalog(tuple(definitions))


def bundled_skill_manifest_diagnostics() -> tuple[str, ...]:
    """Compare parsed Skill manifests to the current host catalog declarations.

    The host catalog remains the runtime source for QRE-122; diagnostics make
    drift explicit before the later contract swaps the authority.
    """
    from fleet_rlm.skills.manifest import parse_bundled_skill_manifest

    root = files("fleet_rlm.skills").joinpath("bundled")
    diagnostics: list[str] = []
    with as_file(root) as root_path:
        for spec in _BUNDLED_SPECS:
            directory = root_path.joinpath(spec.name)
            try:
                manifest = parse_bundled_skill_manifest(directory)
            except Exception as exc:
                diagnostics.append(f"{spec.name}: manifest parse failed: {exc}")
                continue
            expected = {
                "name": spec.name,
                "description": spec.description,
                "version": spec.version,
                "affordances": spec.affordances,
                "resources": spec.resources,
            }
            actual = {
                "name": manifest.name,
                "description": manifest.description,
                "version": manifest.version,
                "affordances": manifest.affordances,
                "resources": tuple((resource.path, resource.media_type) for resource in manifest.resources),
            }
            for field, expected_value in expected.items():
                if actual[field] != expected_value:
                    diagnostics.append(
                        f"{spec.name}: manifest {field} differs from host catalog "
                        f"({actual[field]!r} != {expected_value!r})"
                    )
    return tuple(diagnostics)
