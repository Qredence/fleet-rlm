"""Unit contracts for gated scoring."""

from __future__ import annotations

import pytest

from fleet_rlm.optimization.scoring import score_evaluation


def test_score_is_bounded_after_all_gates_pass() -> None:
    result = score_evaluation(
        typed_output_valid=True,
        quality=1.0,
        grounding=1.0,
        execution_safe=True,
        evaluator_available=True,
        iterations=999,
        submodel_calls=999,
        elapsed_seconds=999.0,
    )
    assert result.eligible is True
    assert 0.0 <= result.score <= 1.0
    assert result.efficiency_penalty == 0.15


@pytest.mark.parametrize(
    "changes, category",
    [
        ({"typed_output_valid": False}, "typed_output_invalid"),
        ({"execution_safe": False}, "execution_safety_failed"),
        ({"evaluator_available": False}, "evaluator_unavailable"),
        ({"quality": None}, "quality_or_grounding_failed"),
    ],
)
def test_mandatory_gate_failures_fail_closed(changes: dict, category: str) -> None:
    kwargs = {
        "typed_output_valid": True,
        "quality": 0.8,
        "grounding": 0.9,
        "execution_safe": True,
        "evaluator_available": True,
        "iterations": 1,
        "submodel_calls": 1,
        "elapsed_seconds": 1.0,
    }
    kwargs.update(changes)
    result = score_evaluation(**kwargs)
    assert result.score == 0.0
    assert result.eligible is False
    assert result.failure_category == category


def test_scores_require_normalized_components() -> None:
    with pytest.raises(ValueError, match="quality"):
        score_evaluation(
            typed_output_valid=True,
            quality=1.1,
            grounding=0.8,
            execution_safe=True,
            evaluator_available=True,
            iterations=0,
            submodel_calls=0,
            elapsed_seconds=0.0,
        )
