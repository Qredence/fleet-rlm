"""Pure exact bundled Skill selection."""

from uuid import uuid4

import pytest

from fleet_rlm.skills.catalog import SkillCatalog, build_bundled_skill_catalog
from fleet_rlm.skills.errors import InvalidSkillSelectionError
from fleet_rlm.skills.models import SkillCard, SkillDefinition, SkillSelectionRef
from fleet_rlm.skills.resolver import resolve_selected_skills
from fleet_rlm.skills.signatures import DataAnalysisSignature


def test_resolver_accepts_exact_ordered_selection() -> None:
    catalog = build_bundled_skill_catalog()
    cards = catalog.cards()
    resolved = resolve_selected_skills(
        catalog,
        tuple(SkillSelectionRef(card.id, card.version) for card in reversed(cards)),
    )
    assert [skill.card.name for skill in resolved.selected] == ["workspace-files", "long-context"]
    assert resolved.instructions == tuple(skill.instructions for skill in resolved.selected)


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
