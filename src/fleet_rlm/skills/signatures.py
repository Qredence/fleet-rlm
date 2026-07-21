"""Narrow optional DSPy Signature extension for bundled Skills."""

from __future__ import annotations

import dspy

_REQUIRED_INPUTS = {
    "request": str,
    "session_context": dict,
    "skill_cards": list[dict],
    "attachments": list[dict],
}


class DataAnalysisSignature(dspy.Signature):
    """Analyze supplied data with deterministic verification and concise findings."""

    request: str = dspy.InputField()
    session_context: dict = dspy.InputField()
    skill_cards: list[dict] = dspy.InputField()
    attachments: list[dict] = dspy.InputField()
    answer: str = dspy.OutputField(desc="User-facing analysis")
    findings: list[str] = dspy.OutputField(desc="Verified key findings")
    metrics: list[dict] = dspy.OutputField(desc="Named computed metrics")
    anomalies: list[str] = dspy.OutputField(desc="Qualified anomalies or an empty list")


def validate_skill_signature(signature: type[dspy.Signature]) -> None:
    """Require Fleet inputs and one canonical required text output."""

    if not isinstance(signature, type) or not issubclass(signature, dspy.Signature):
        raise ValueError("Skill Signature must be a dspy.Signature")
    for name, annotation in _REQUIRED_INPUTS.items():
        field = signature.fields.get(name)
        extra = getattr(field, "json_schema_extra", None) if field is not None else None
        if (
            field is None
            or not isinstance(extra, dict)
            or extra.get("__dspy_field_type") != "input"
            or field.annotation != annotation
            or not field.is_required()
        ):
            raise ValueError(f"Skill Signature must define input field: {name}")
    answer = signature.fields.get("answer")
    extra = getattr(answer, "json_schema_extra", None) if answer is not None else None
    if (
        answer is None
        or not isinstance(extra, dict)
        or extra.get("__dspy_field_type") != "output"
        or answer.annotation is not str
        or not answer.is_required()
    ):
        raise ValueError("Skill Signature must define required output field: answer")
