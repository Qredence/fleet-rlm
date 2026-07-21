"""Optional bundled Skill Signature contract."""

import dspy
import pytest

from fleet_rlm.skills.signatures import DataAnalysisSignature, validate_skill_signature


def test_data_analysis_signature_preserves_standard_inputs_and_answer() -> None:
    assert {"request", "session_context", "skill_cards", "attachments"} <= set(DataAnalysisSignature.input_fields)
    assert DataAnalysisSignature.output_fields["answer"].annotation is str
    validate_skill_signature(DataAnalysisSignature)


@pytest.mark.parametrize("variant", ["missing", "output", "optional", "wrong_type"])
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


@pytest.mark.parametrize("variant", ["missing", "input", "optional", "wrong_type"])
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
