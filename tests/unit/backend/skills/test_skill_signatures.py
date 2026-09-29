"""Optional bundled Skill Signature contract."""

import dspy
import pytest

from fleet_rlm.rlm.program import (
    AttachmentInput,
    FleetRLMSignature,
    SessionContextInput,
    SkillCardInput,
)
from fleet_rlm.skills.signatures import validate_skill_signature


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
