"""Trusted GEPA metric contracts stay bounded and fail closed."""

from __future__ import annotations

import dspy
import pytest

from fleet_rlm.optimization.metric import (
    ScoreFeedback,
    TrustedGEPAFeedbackMetric,
    TrustedMetricError,
    expectation_score,
    scorer_policy_sha256,
)


def _gold(expectations: dict) -> dict:
    return {"expectations": expectations}


def test_expectation_metric_returns_exact_prediction_feedback():
    result = expectation_score(_gold({"expected_response": "703"}), {"answer": "703"})
    assert result == ScoreFeedback(1.0, "all machine-checkable expectations passed")

    metric = TrustedGEPAFeedbackMetric(lambda _gold, _pred, **_kwargs: result, "a" * 64)
    prediction = metric(_gold({"expected_response": "703"}), {"answer": "703"})
    assert isinstance(prediction, dspy.Prediction)
    assert prediction.score == 1.0
    assert prediction.feedback == "all machine-checkable expectations passed"


def test_qualitative_only_expectations_are_not_silently_scored_as_pass():
    result = expectation_score(_gold({"criteria": ["Explain the evidence."]}), {"answer": "anything"})
    assert result.score == 0.0
    assert "unscorable" in result.feedback


@pytest.mark.parametrize(
    "feedback",
    ["", "api_key leaked", "token=secret", "x" * 2001],
)
def test_score_feedback_rejects_sensitive_or_unbounded_feedback(feedback: str):
    with pytest.raises(TrustedMetricError):
        ScoreFeedback(0.5, feedback)


def test_metric_failure_is_bounded_and_does_not_expose_exception_text():
    metric = TrustedGEPAFeedbackMetric(
        lambda _gold, _pred, **_kwargs: (_ for _ in ()).throw(RuntimeError("password=do-not-expose")),
        "b" * 64,
    )
    result = metric({}, {})
    assert result.score == 0.0
    assert result.feedback == "trusted_scorer_failure: RuntimeError"
    assert "password" not in result.feedback


def test_metric_requires_digest_and_policy_hash_is_stable():
    with pytest.raises(TrustedMetricError):
        TrustedGEPAFeedbackMetric(lambda _gold, _pred, **_kwargs: ScoreFeedback(1.0, "ok"), "short")
    assert scorer_policy_sha256({"model": "judge-v1", "temperature": 0}) == scorer_policy_sha256(
        {"temperature": 0, "model": "judge-v1"}
    )
