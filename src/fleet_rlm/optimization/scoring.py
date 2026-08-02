"""Hard-gated, bounded scoring for signature optimization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

FailureCategory = Literal[
    "typed_output_invalid",
    "quality_or_grounding_failed",
    "execution_safety_failed",
    "evaluator_unavailable",
]


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """Trusted-host score and gate evidence for one candidate evaluation."""

    score: float
    eligible: bool
    base_quality: float
    efficiency_penalty: float
    failure_category: FailureCategory | None
    feedback: dict[str, Any]


def score_evaluation(
    *,
    typed_output_valid: bool,
    quality: float | None,
    grounding: float | None,
    execution_safe: bool,
    evaluator_available: bool,
    iterations: int,
    submodel_calls: int,
    elapsed_seconds: float,
) -> ScoreResult:
    """Apply mandatory gates then calculate a bounded quality score."""
    gate_failure = _gate_failure(
        typed_output_valid=typed_output_valid,
        execution_safe=execution_safe,
        evaluator_available=evaluator_available,
        quality=quality,
        grounding=grounding,
    )
    if gate_failure is not None:
        return ScoreResult(
            score=0.0,
            eligible=False,
            base_quality=0.0,
            efficiency_penalty=0.0,
            failure_category=gate_failure,
            feedback={"failure_category": gate_failure},
        )

    assert quality is not None
    assert grounding is not None
    quality_score = _normalized(quality, "quality")
    grounding_score = _normalized(grounding, "grounding")
    base_quality = 0.7 * quality_score + 0.3 * grounding_score
    penalty = min(
        0.15,
        max(0, iterations) * 0.003 + max(0, submodel_calls) * 0.004 + max(0.0, elapsed_seconds) * 0.0005,
    )
    return ScoreResult(
        score=max(0.0, min(1.0, base_quality - penalty)),
        eligible=True,
        base_quality=base_quality,
        efficiency_penalty=penalty,
        failure_category=None,
        feedback={
            "typed_output_valid": True,
            "quality": quality_score,
            "grounding": grounding_score,
            "execution_safe": True,
            "evaluator_available": True,
        },
    )


def _gate_failure(
    *,
    typed_output_valid: bool,
    quality: float | None,
    grounding: float | None,
    execution_safe: bool,
    evaluator_available: bool,
) -> FailureCategory | None:
    if not evaluator_available:
        return "evaluator_unavailable"
    if not typed_output_valid:
        return "typed_output_invalid"
    if not execution_safe:
        return "execution_safety_failed"
    if quality is None or grounding is None:
        return "quality_or_grounding_failed"
    return None


def _normalized(value: float, name: str) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be normalized to [0, 1]")
    return value


__all__ = ["FailureCategory", "ScoreResult", "score_evaluation"]
