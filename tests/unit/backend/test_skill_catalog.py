"""Immutable bundled Skill model and catalog contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from fleet_rlm.skills.catalog import build_bundled_skill_catalog, stable_skill_id
from fleet_rlm.skills.models import SkillCard, SkillDefinition, SkillResource, SkillSelectionRef
from fleet_rlm.skills.signatures import DataAnalysisSignature


def test_bundled_catalog_is_fixed_sorted_and_version_stable() -> None:
    catalog = build_bundled_skill_catalog()
    assert [(card.name, card.version) for card in catalog.cards()] == [
        ("data-analysis", "1.0.0"),
        ("long-context", "2.0.0"),
        ("report-builder", "1.0.0"),
        ("workspace-files", "1.0.0"),
    ]
    assert all(card.id == stable_skill_id(card.name) for card in catalog.cards())
    assert str(catalog.require(stable_skill_id("data-analysis")).card.id) == ("f4d260fa-a663-5ef9-835f-eac46c10c1bf")
    assert str(catalog.require(stable_skill_id("report-builder")).card.id) == ("90bd89fb-66c8-558d-acdb-55c59ba7106c")
    assert catalog.require(stable_skill_id("data-analysis")).signature is DataAnalysisSignature
    assert catalog.require(stable_skill_id("report-builder")).signature is None


def test_catalog_contains_only_explicit_utf8_resources() -> None:
    catalog = build_bundled_skill_catalog()
    long_context = catalog.require(stable_skill_id("long-context"))
    workspace = catalog.require(stable_skill_id("workspace-files"))
    data_analysis = catalog.require(stable_skill_id("data-analysis"))
    report_builder = catalog.require(stable_skill_id("report-builder"))
    assert tuple(long_context.resources) == (
        "scripts/semantic_chunk.py",
        "scripts/rank_chunks.py",
        "references/chunking-strategies.md",
    )
    assert tuple(workspace.resources) == ("references/filesystem-contract.md",)
    assert all(
        isinstance(resource.content, str)
        for skill in (long_context, workspace)
        for resource in skill.resources.values()
    )
    assert not any(path.endswith(".pdf") for skill in (long_context, workspace) for path in skill.resources)
    assert data_analysis.resources == {}
    assert report_builder.resources == {}


def test_models_are_immutable_and_validate_paths_versions_and_bodies() -> None:
    card = SkillCard(uuid4(), "example", "Example workflow", "1.0.0", True)
    resource = SkillResource("references/guide.md", "text/markdown", "Guide")
    skill = SkillDefinition(card, "Instructions", {resource.path: resource})
    catalog = build_bundled_skill_catalog()
    with pytest.raises(FrozenInstanceError):
        skill.instructions = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        skill.resources["other.md"] = resource  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        catalog._cards = ()  # type: ignore[misc]
    for path in ("/absolute.md", "../escape.md", "a/../escape.md", "./guide.md"):
        with pytest.raises(ValueError, match="path"):
            SkillResource(path, "text/markdown", "body")
    with pytest.raises(ValueError, match="instructions"):
        SkillDefinition(SkillCard(uuid4(), "empty", "Empty", "1", False), "")
    with pytest.raises(ValueError, match="version"):
        SkillSelectionRef(uuid4(), "")
