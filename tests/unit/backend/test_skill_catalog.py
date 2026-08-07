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
        ("dspy-rlm", "1.0.0"),
        ("long-context", "2.0.0"),
        ("report-builder", "1.0.0"),
        ("workspace-files", "1.0.0"),
    ]
    assert all(card.id == stable_skill_id(card.name) for card in catalog.cards())
    assert str(catalog.require(stable_skill_id("data-analysis")).card.id) == ("f4d260fa-a663-5ef9-835f-eac46c10c1bf")
    assert str(catalog.require(stable_skill_id("report-builder")).card.id) == ("90bd89fb-66c8-558d-acdb-55c59ba7106c")
    assert catalog.require(stable_skill_id("data-analysis")).signature is DataAnalysisSignature
    assert catalog.require(stable_skill_id("report-builder")).signature is None


def test_dspy_rlm_skill_defines_recursive_not_retrieval_language_model() -> None:
    catalog = build_bundled_skill_catalog()
    skill = catalog.require(stable_skill_id("dspy-rlm"))
    assert "Recursive Language Model" in skill.instructions
    assert "Never call it a Retrieval Language Model" in skill.instructions
    resource = skill.resources["references/rlm-contract.md"]
    assert "Recursive Language Model" in resource.content
    assert "Retrieval Language Model" in resource.content
    assert "dspy.Retrieve" in resource.content
    assert "Turn output is too large" in resource.content
    assert "active Fleet Signature" in resource.content
    assert "every required output field" in resource.content
    assert "`max_iterations`" in resource.content
    assert "| Fleet iteration budget | `max_iterations` | `max_iters` |" in resource.content
    assert "caller-owned interpreter" in resource.content
    assert '["skill_markdown"]' in skill.instructions
    assert '["content"]' in skill.instructions
    assert "not" in resource.content.lower()


def test_workspace_skills_distinguish_exact_readback_from_large_file_metadata_confirmation() -> None:
    catalog = build_bundled_skill_catalog()
    workspace = catalog.require(stable_skill_id("workspace-files")).instructions
    report_builder = catalog.require(stable_skill_id("report-builder")).instructions

    for instructions in (workspace, report_builder):
        assert "10,000 characters" in instructions
        assert 'len(content.encode("utf-8"))' in instructions
        assert "metadata confirmation" in instructions
        assert "prefix read" not in instructions


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
    assert by_name["long-context"].affordances == ("fetch_url", "llm_query_batched", "workspace.files")
    assert by_name["workspace-files"].affordances == ("workspace.files", "artifacts.publish")
    assert by_name["data-analysis"].affordances == ("artifacts.publish", "llm_query_batched")
    assert by_name["report-builder"].affordances == ("workspace.files", "artifacts.publish")
    assert by_name["dspy-rlm"].affordances == ("interpreter", "llm_query")
    assert all(isinstance(card.affordances, tuple) for card in catalog.cards())
    assert all(len(card.affordances) <= 8 for card in catalog.cards())
