"""Immutable bundled Skill model and catalog contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from fleet_rlm.skills.catalog import build_bundled_skill_catalog, stable_skill_id
from fleet_rlm.skills.errors import InvalidSkillSelectionError
from fleet_rlm.skills.models import SkillCard, SkillDefinition, SkillResource, SkillSelectionRef
from fleet_rlm.skills.resolver import resolve_selected_skills, resolved_signature
from fleet_rlm.skills.signatures import DataAnalysisSignature


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


def test_resolver_accepts_exact_ordered_selection() -> None:
    catalog = build_bundled_skill_catalog()
    cards = tuple(card for card in catalog.cards() if card.name != "dspy-rlm")
    resolved = resolve_selected_skills(
        catalog,
        tuple(SkillSelectionRef(card.id, card.version) for card in reversed(cards)),
    )
    assert [skill.card.name for skill in resolved.selected] == [
        "workspace-files",
        "report-builder",
        "long-context",
        "data-analysis",
    ]
    assert resolved.instructions == tuple(skill.instructions for skill in resolved.selected)


def test_explicit_selection_advertises_only_the_authorized_selected_cards() -> None:
    catalog = build_bundled_skill_catalog()
    selected = tuple(card for card in catalog.cards() if card.name in {"data-analysis", "long-context"})
    assert len(selected) == 2

    resolved = resolve_selected_skills(
        catalog,
        tuple(SkillSelectionRef(card.id, card.version) for card in selected),
    )

    assert resolved.cards == selected
    assert {card.name for card in resolved.cards} == {"data-analysis", "long-context"}


def test_resolver_rejects_unknown_duplicate_overflow_and_version_mismatch() -> None:
    catalog = build_bundled_skill_catalog()
    card = catalog.cards()[0]
    invalid = (
        (SkillSelectionRef(uuid4(), "1.0.0"),),
        (SkillSelectionRef(card.id, card.version), SkillSelectionRef(card.id, card.version)),
        (SkillSelectionRef(card.id, "0.0.0"),),
    )
    for values in invalid:
        with pytest.raises(InvalidSkillSelectionError):
            resolve_selected_skills(catalog, values)
    with pytest.raises(InvalidSkillSelectionError):
        resolve_selected_skills(catalog, (), max_selections=-1)


def test_runner_signature_recomposition_uses_actual_policy_without_duplicate_bodies() -> None:
    from fleet_rlm.rlm.program import root_signature_for_recursion

    catalog = build_bundled_skill_catalog()
    data_analysis = catalog.require(catalog.cards()[0].id)
    resolved = resolve_selected_skills(
        catalog,
        (SkillSelectionRef(data_analysis.card.id, data_analysis.card.version),),
    )
    prepared = resolved_signature(resolved)

    recomposed = root_signature_for_recursion(
        prepared,
        recursion_enabled=False,
        skill_instructions=resolved.instructions,
    )

    assert "rlm_query(task=task, inputs=inputs" not in recomposed.instructions
    assert recomposed.instructions.count("Compute only the requested metrics") == 1
    assert recomposed.output_fields.keys() == DataAnalysisSignature.output_fields.keys()
