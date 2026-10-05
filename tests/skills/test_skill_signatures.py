"""Optional bundled Skill Signature contract."""

import dspy
import pytest

from fleet_rlm.rlm.program import (
    AttachmentInput,
    FleetRLMSignature,
    SessionContextInput,
    SkillCardInput,
)
from fleet_rlm.skills.catalog import build_bundled_skill_catalog, stable_skill_id
from fleet_rlm.skills.signatures import DataAnalysisSignature, validate_skill_signature


def test_default_signature_uses_strict_model_visible_input_types() -> None:
    assert FleetRLMSignature.input_fields["request"].annotation is str
    assert FleetRLMSignature.input_fields["session_context"].annotation is SessionContextInput
    assert FleetRLMSignature.input_fields["skill_cards"].annotation == list[SkillCardInput]
    assert FleetRLMSignature.input_fields["attachments"].annotation == list[AttachmentInput]
    assert FleetRLMSignature.output_fields["answer"].annotation is str


@pytest.mark.parametrize("variant", ["missing", "output", "optional"])
def test_signature_rejects_invalid_standard_input(variant: str) -> None:
    annotations: dict[str, object] = {
        "request": str,
        "session_context": dict,
        "skill_cards": list[dict],
        "attachments": list[dict],
        "answer": str,
    }
    namespace: dict[str, object] = {"__annotations__": annotations, "answer": dspy.OutputField()}
    for name in ("request", "session_context", "skill_cards", "attachments"):
        namespace[name] = dspy.InputField()
    if variant == "missing":
        annotations.pop("request")
        namespace.pop("request")
    elif variant == "output":
        namespace["request"] = dspy.OutputField()
    elif variant == "optional":
        annotations["request"] = str | None
    else:
        annotations["request"] = int
    invalid = type("InvalidInputSignature", (dspy.Signature,), namespace)
    with pytest.raises(ValueError, match="request"):
        validate_skill_signature(invalid)


@pytest.mark.parametrize("variant", ["missing", "input", "optional"])
def test_signature_rejects_invalid_answer(variant: str) -> None:
    namespace = {
        "__annotations__": {
            "request": str,
            "session_context": dict,
            "skill_cards": list[dict],
            "attachments": list[dict],
        },
        "request": dspy.InputField(),
        "session_context": dspy.InputField(),
        "skill_cards": dspy.InputField(),
        "attachments": dspy.InputField(),
    }
    if variant != "missing":
        namespace["__annotations__"]["answer"] = (
            str | None if variant == "optional" else int if variant == "wrong_type" else str
        )
        namespace["answer"] = dspy.InputField() if variant == "input" else dspy.OutputField()
    invalid = type("InvalidSignature", (dspy.Signature,), namespace)
    with pytest.raises(ValueError, match="answer"):
        validate_skill_signature(invalid)


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
