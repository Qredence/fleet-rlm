"""GEPA optimization specs for curated runtime DSPy signatures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import dspy

from fleet_rlm.quality.module_registry import MetricProfile, ModuleOptimizationSpec, register_module
from fleet_rlm.quality.scoring_helpers import set_overlap_score
from fleet_rlm.runtime.agent.signatures import (
    ClarificationQuestionSignature,
    CodeChangePlan,
    ExtractFromLogs,
    IncidentTriageFromLogs,
    MemoryActionIntentSignature,
    SummarizeLongDocument,
)


@dataclass(frozen=True, slots=True)
class RuntimeSignatureTarget:
    slug: str
    runtime_module_name: str
    signature: type[dspy.Signature]
    label: str
    description: str


class RuntimeSignatureProgram(dspy.Module):
    """Single-predictor DSPy program for optimizing a runtime signature."""

    def __init__(self, signature: type[dspy.Signature]) -> None:
        super().__init__()
        self.predict = dspy.Predict(signature)

    def forward(self, **kwargs: Any) -> dspy.Prediction:
        return self.predict(**kwargs)


def _field_names(signature: type[dspy.Signature], kind: str) -> list[str]:
    fields = signature.input_fields if kind == "input" else signature.output_fields
    return list(fields.keys())


def _coerce_rows_to_examples(
    rows: list[dict[str, Any]],
    *,
    input_keys: list[str],
) -> list[dspy.Example]:
    return [dspy.Example(**row).with_inputs(*input_keys) for row in rows]


def _normalize_text(value: Any) -> str:
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, default=str, sort_keys=True)
    return " ".join(str(value or "").strip().split())


def _score_text(expected: Any, actual: Any) -> tuple[float, str]:
    expected_text = _normalize_text(expected)
    actual_text = _normalize_text(actual)
    if not expected_text:
        return (1.0 if not actual_text else 0.0), "Reference text is empty."
    if expected_text == actual_text:
        return 1.0, "Text exactly matches."
    expected_lower = expected_text.lower()
    actual_lower = actual_text.lower()
    if expected_lower in actual_lower or actual_lower in expected_lower:
        return 0.75, "Text substantially contains the reference."
    similarity = SequenceMatcher(None, expected_lower, actual_lower).ratio()
    if similarity >= 0.55:
        return 0.5, "Text is related but not exact."
    return 0.0, f"Text differs from the reference. Expected {_normalize_text(expected)[:120]!r}."


def _score_list(expected: list[Any], actual: Any) -> tuple[float, str]:
    actual_list = actual if isinstance(actual, list) else []
    expected_set = {json.dumps(item, default=str, sort_keys=True) for item in expected}
    actual_set = {json.dumps(item, default=str, sort_keys=True) for item in actual_list}
    score = set_overlap_score(expected_set, actual_set)
    return score, f"List overlap score={score:.3f}."


def _score_value(field_name: str, expected: Any, actual: Any) -> tuple[float, str]:
    if isinstance(expected, bool):
        score = 1.0 if bool(actual) is expected else 0.0
        return score, f"{field_name}: boolean {'matches' if score else 'does not match'}."
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            actual_number = float(actual)
        except (TypeError, ValueError):
            return 0.0, f"{field_name}: expected numeric output."
        score = 1.0 if float(expected) == actual_number else max(0.0, 1.0 - abs(float(expected) - actual_number))
        return min(1.0, score), f"{field_name}: numeric score={min(1.0, score):.3f}."
    if isinstance(expected, list):
        return _score_list(expected, actual)
    if isinstance(expected, dict):
        return _score_text(expected, actual)
    return _score_text(expected, actual)


def _build_signature_metric(output_keys: list[str]) -> Any:
    def metric(
        gold: Any,
        pred: Any,
        trace: Any = None,
        pred_name: str | None = None,
        pred_trace: Any = None,
    ) -> Any:
        from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback

        _ = trace, pred_name, pred_trace
        weighted_scores: list[float] = []
        feedback_parts: list[str] = []
        for key in output_keys:
            expected = getattr(gold, key, None)
            actual = getattr(pred, key, None)
            score, feedback = _score_value(key, expected, actual)
            weighted_scores.append(score)
            feedback_parts.append(f"{key}: {feedback}")
        aggregate = sum(weighted_scores) / len(weighted_scores) if weighted_scores else 0.0
        return ScoreWithFeedback(score=aggregate, feedback=" ".join(feedback_parts))

    metric.__name__ = "runtime_signature_feedback_metric"
    return metric


TARGETS: tuple[RuntimeSignatureTarget, ...] = (
    RuntimeSignatureTarget(
        slug="summarize-long-document",
        runtime_module_name="summarize_long_document",
        signature=SummarizeLongDocument,
        label="Summarize Long Document",
        description="Optimize the long-document summary signature prompt.",
    ),
    RuntimeSignatureTarget(
        slug="extract-from-logs",
        runtime_module_name="extract_from_logs",
        signature=ExtractFromLogs,
        label="Extract From Logs",
        description="Optimize the log extraction signature prompt.",
    ),
    RuntimeSignatureTarget(
        slug="triage-incident-logs",
        runtime_module_name="triage_incident_logs",
        signature=IncidentTriageFromLogs,
        label="Triage Incident Logs",
        description="Optimize the incident triage signature prompt.",
    ),
    RuntimeSignatureTarget(
        slug="plan-code-change",
        runtime_module_name="plan_code_change",
        signature=CodeChangePlan,
        label="Plan Code Change",
        description="Optimize the structured code-change planning signature prompt.",
    ),
    RuntimeSignatureTarget(
        slug="clarification-questions",
        runtime_module_name="clarification_questions",
        signature=ClarificationQuestionSignature,
        label="Clarification Questions",
        description="Optimize clarification-question planning for ambiguous work.",
    ),
    RuntimeSignatureTarget(
        slug="memory-action-intent",
        runtime_module_name="memory_action_intent",
        signature=MemoryActionIntentSignature,
        label="Memory Action Intent",
        description="Optimize memory action classification and safety intent.",
    ),
)


def spec_for_runtime_signature(target: RuntimeSignatureTarget) -> ModuleOptimizationSpec:
    input_keys = _field_names(target.signature, "input")
    output_keys = _field_names(target.signature, "output")
    return ModuleOptimizationSpec(
        module_slug=target.slug,
        label=target.label,
        program_spec=f"{target.signature.__module__}:{target.signature.__name__}",
        artifact_filename=f"{target.slug}.optimized.json",
        input_keys=input_keys,
        required_dataset_keys=[*input_keys, *output_keys],
        module_factory=lambda signature=target.signature: RuntimeSignatureProgram(signature),
        row_converter=lambda rows, keys=input_keys: _coerce_rows_to_examples(rows, input_keys=keys),
        metric_builder=lambda keys=output_keys: _build_signature_metric(keys),
        metric_name="runtime_signature_feedback_metric",
        description=target.description,
        runtime_module_name=target.runtime_module_name,
        signature_class_name=target.signature.__name__,
        output_keys=output_keys,
        optimization_target_kind="runtime-signature",
        metric_profile=MetricProfile(profile_id=target.slug),
    )


for _target in TARGETS:
    register_module(spec_for_runtime_signature(_target))
