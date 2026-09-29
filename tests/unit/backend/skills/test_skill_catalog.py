"""Immutable bundled Skill model and catalog contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from fleet_rlm.skills.catalog import build_bundled_skill_catalog, stable_skill_id
from fleet_rlm.skills.models import SkillCard, SkillDefinition, SkillResource, SkillSelectionRef


def test_catalog_contains_only_explicit_utf8_resources() -> None:
    catalog = build_bundled_skill_catalog()
    dspy_rlm = catalog.require(stable_skill_id("dspy-rlm"))
    long_context = catalog.require(stable_skill_id("long-context"))
    workspace = catalog.require(stable_skill_id("workspace-files"))
    data_analysis = catalog.require(stable_skill_id("data-analysis"))
    report_builder = catalog.require(stable_skill_id("report-builder"))
    assert tuple(dspy_rlm.resources) == ("references/rlm-contract.md",)
    assert tuple(long_context.resources) == (
        "scripts/semantic_chunk.py",
        "scripts/rank_chunks.py",
        "references/chunking-strategies.md",
    )
    assert tuple(workspace.resources) == ("references/filesystem-contract.md",)
    assert all(
        isinstance(resource.content, str)
        for skill in (dspy_rlm, long_context, workspace)
        for resource in skill.resources.values()
    )
    assert not any(path.endswith(".pdf") for skill in (dspy_rlm, long_context, workspace) for path in skill.resources)
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


def test_bundled_cards_advertise_bounded_capability_affordances() -> None:
    """Cards name the capability families each Skill expects so the model and
    operator see them before loading; affordances stay closed and bounded."""

    catalog = build_bundled_skill_catalog()
    by_name = {card.name: card for card in catalog.cards()}
    assert by_name["long-context"].affordances == ("sandbox.search", "llm_query_batched", "workspace.files")
    assert by_name["workspace-files"].affordances == ("workspace.files", "artifacts.publish")
    assert by_name["data-analysis"].affordances == ("artifacts.publish", "llm_query_batched")
    assert by_name["report-builder"].affordances == ("workspace.files", "artifacts.publish")
    assert by_name["dspy-rlm"].affordances == ("interpreter", "llm_query")
    assert all(isinstance(card.affordances, tuple) for card in catalog.cards())
    assert all(len(card.affordances) <= 8 for card in catalog.cards())
