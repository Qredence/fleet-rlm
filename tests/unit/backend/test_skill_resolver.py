"""Pure exact bundled Skill selection."""

from uuid import uuid4

import pytest

from fleet_rlm.skills.catalog import SkillCatalog, build_bundled_skill_catalog
from fleet_rlm.skills.errors import InvalidSkillSelectionError
from fleet_rlm.skills.models import SkillCard, SkillDefinition, SkillSelectionRef
from fleet_rlm.skills.resolver import resolve_selected_skills, resolved_signature
from fleet_rlm.skills.signatures import DataAnalysisSignature


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


def test_resolver_rejects_two_signature_skills() -> None:
    definitions = tuple(
        SkillDefinition(
            SkillCard(uuid4(), f"signed-{index}", "Signed", "1", False), "Use it", signature=DataAnalysisSignature
        )
        for index in range(2)
    )
    catalog = SkillCatalog(definitions)
    selections = tuple(SkillSelectionRef(skill.card.id, "1") for skill in definitions)
    with pytest.raises(InvalidSkillSelectionError):
        resolve_selected_skills(catalog, selections)


def test_dspy_rlm_selection_preserves_selected_signature_outputs() -> None:
    catalog = build_bundled_skill_catalog()
    selections = tuple(
        SkillSelectionRef(catalog.require(catalog.cards()[index].id).card.id, catalog.cards()[index].version)
        for index in (0, 1)
    )
    resolved = resolve_selected_skills(catalog, selections)

    signature = resolved_signature(resolved)
    assert signature.output_fields.keys() == DataAnalysisSignature.output_fields.keys()


def test_data_analysis_signature_keeps_outputs_and_receives_fleet_operating_policy() -> None:
    catalog = build_bundled_skill_catalog()
    data_analysis = catalog.require(catalog.cards()[0].id)
    assert data_analysis.signature is DataAnalysisSignature
    resolved = resolve_selected_skills(
        catalog,
        (SkillSelectionRef(data_analysis.card.id, data_analysis.card.version),),
    )

    signature = resolved_signature(resolved)

    assert set(signature.output_fields) == {"answer", "findings", "metrics", "anomalies"}
    for name, field in DataAnalysisSignature.output_fields.items():
        composed = signature.output_fields[name]
        assert composed.annotation == field.annotation
        assert composed.is_required() == field.is_required()
        assert composed.json_schema_extra == field.json_schema_extra
    assert "Python standard library" in signature.instructions
    assert "llm_query(prompt)" in signature.instructions
    assert "llm_query_batched(prompts)" in signature.instructions
    assert "rlm_query(prompt=prompt)" in signature.instructions
    assert "exactly one typed ``SUBMIT``" in signature.instructions
    assert "Compute only the requested metrics" in signature.instructions
    assert signature.instructions.count("Compute only the requested metrics") == 1


def test_custom_skill_signature_omits_recursive_guidance_when_the_tool_is_unavailable() -> None:
    catalog = build_bundled_skill_catalog()
    data_analysis = catalog.require(catalog.cards()[0].id)
    resolved = resolve_selected_skills(
        catalog,
        (SkillSelectionRef(data_analysis.card.id, data_analysis.card.version),),
    )

    signature = resolved_signature(resolved, recursion_enabled=False)

    assert set(signature.output_fields) == {"answer", "findings", "metrics", "anomalies"}
    assert "rlm_query(prompt=prompt)" not in signature.instructions
    assert "llm_query(prompt)" in signature.instructions
    assert "llm_query_batched(prompts)" in signature.instructions
    assert "5. Verify the result" in signature.instructions


def test_runner_signature_recomposition_uses_actual_policy_without_duplicate_bodies() -> None:
    from fleet_rlm.rlm.signature import root_signature_for_recursion

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

    assert "rlm_query(prompt=prompt)" not in recomposed.instructions
    assert recomposed.instructions.count("Compute only the requested metrics") == 1
    assert recomposed.output_fields.keys() == DataAnalysisSignature.output_fields.keys()
