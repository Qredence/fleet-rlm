"""Pure exact bundled Skill selection."""

from uuid import uuid4

import pytest

from fleet_rlm.skills.catalog import build_bundled_skill_catalog
from fleet_rlm.skills.errors import InvalidSkillSelectionError
from fleet_rlm.skills.models import SkillSelectionRef
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
