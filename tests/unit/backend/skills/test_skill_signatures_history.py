"""P44.4 Skill Signature validator: required `history: dspy.History` input."""

from __future__ import annotations

import dspy
import pytest

from fleet_rlm.skills.catalog import build_bundled_skill_catalog, stable_skill_id
from fleet_rlm.skills.signatures import DataAnalysisSignature, validate_skill_signature


def _build_signature(namespace: dict[str, object]) -> type[dspy.Signature]:
    return type("CustomSkillSignature", (dspy.Signature,), namespace)


@pytest.mark.parametrize("bad_annotation", [dict])
def test_validate_skill_signature_rejects_non_history_annotation(bad_annotation: type) -> None:
    """`history` annotation must be exactly `dspy.History`; dict/list are rejected."""

    namespace: dict[str, object] = {
        "__annotations__": {
            "request": str,
            "history": bad_annotation,
            "session_context": dict,
            "skill_cards": list[dict],
            "attachments": list[dict],
            "answer": str,
        },
        "request": dspy.InputField(),
        "history": dspy.InputField(),
        "session_context": dspy.InputField(),
        "skill_cards": dspy.InputField(),
        "attachments": dspy.InputField(),
        "answer": dspy.OutputField(),
    }
    wrong_typed = _build_signature(namespace)
    with pytest.raises(ValueError, match="history"):
        validate_skill_signature(wrong_typed)


def test_validate_skill_signature_rejects_optional_history() -> None:
    """`history` must be required; an optional `dspy.History | None` is rejected."""

    namespace: dict[str, object] = {
        "__annotations__": {
            "request": str,
            "history": dspy.History | None,
            "session_context": dict,
            "skill_cards": list[dict],
            "attachments": list[dict],
            "answer": str,
        },
        "request": dspy.InputField(),
        "history": dspy.InputField(),
        "session_context": dspy.InputField(),
        "skill_cards": dspy.InputField(),
        "attachments": dspy.InputField(),
        "answer": dspy.OutputField(),
    }
    optional_history = _build_signature(namespace)
    with pytest.raises(ValueError, match="history"):
        validate_skill_signature(optional_history)


def test_bundled_catalog_loads_data_analysis_skill_entry() -> None:
    """The bundled catalog still requires DataAnalysisSignature and exposes the entry."""

    catalog = build_bundled_skill_catalog()
    data_analysis = catalog.require(stable_skill_id("data-analysis"))
    assert data_analysis.signature is DataAnalysisSignature
    validate_skill_signature(data_analysis.signature)
    assert data_analysis.card.name == "data-analysis"
    assert "history" in data_analysis.signature.input_fields
    assert data_analysis.signature.input_fields["history"].annotation is dspy.History
