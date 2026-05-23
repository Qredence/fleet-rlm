from __future__ import annotations

import pytest

from fleet_rlm.quality.workspace_metrics import (
    completeness_feedback_metric,
    exact_match_feedback_metric,
    workspace_feedback_metric,
    workspace_score_metric,
)


@pytest.mark.parametrize(
    ("expected", "actual", "score", "snippet"),
    [
        ("hello", "hello", 1.0, "Exact match"),
        ("Hello", "hello", 0.8, "casing"),
        ("hello", "hello world", 0.6, "substring"),
        ("hello", "", 0.0, "Empty response"),
    ],
)
def test_exact_match_feedback_metric_returns_useful_feedback(expected, actual, score, snippet) -> None:
    result_score, feedback = exact_match_feedback_metric(
        {"assistant_response": expected},
        {"assistant_response": actual},
    )

    assert result_score == score
    assert snippet in feedback


def test_completeness_feedback_metric_reports_missing_terms() -> None:
    score, feedback = completeness_feedback_metric(
        {"assistant_response": "machine learning optimization pipeline"},
        {"assistant_response": "machine learning"},
    )

    assert 0.0 < score < 0.6
    assert "optimization" in feedback
    assert "pipeline" in feedback


def test_workspace_metrics_share_the_same_weighted_score() -> None:
    feedback_score, feedback = workspace_feedback_metric(
        {"assistant_response": "hello world"},
        {"assistant_response": "hello"},
    )
    numeric_score = workspace_score_metric(
        {"assistant_response": "hello world"},
        {"assistant_response": "hello"},
    )

    assert feedback_score == pytest.approx(0.2)
    assert numeric_score == pytest.approx(feedback_score)
    assert "exact_match=0.00" in feedback
    assert "completeness=0.50" in feedback
