"""Tests for ``fleet_rlm.runtime.quality.gepa_optimization``."""

from __future__ import annotations

from types import SimpleNamespace

import dspy

from fleet_rlm.runtime.quality.gepa_optimization import (
    build_gepa_feedback_metric,
)


def _gold(text: str) -> SimpleNamespace:
    return SimpleNamespace(assistant_response=text)


def _pred(text: str) -> SimpleNamespace:
    return SimpleNamespace(assistant_response=text)


class TestBuildGepaFeedbackMetric:
    def test_default_metric_returns_score_with_feedback(self) -> None:
        metric = build_gepa_feedback_metric()
        result = metric(_gold("hello"), _pred("hello"))
        # ScoreWithFeedback or float
        from dspy.teleprompt.gepa.gepa import ScoreWithFeedback

        assert isinstance(result, ScoreWithFeedback)
        assert result.score == 1.0
        assert isinstance(result.feedback, str)

    def test_default_metric_mismatch(self) -> None:
        metric = build_gepa_feedback_metric()
        result = metric(_gold("hello"), _pred("goodbye"))
        from dspy.teleprompt.gepa.gepa import ScoreWithFeedback

        assert isinstance(result, ScoreWithFeedback)
        assert result.score < 1.0

    def test_custom_score_fn_scalar(self) -> None:
        def custom(gold, pred, trace=None):
            return 0.42

        metric = build_gepa_feedback_metric(score_fn=custom)
        result = metric(_gold("a"), _pred("b"))
        assert result == 0.42

    def test_custom_score_fn_tuple(self) -> None:
        def custom(gold, pred, trace=None):
            return 0.7, "custom feedback"

        metric = build_gepa_feedback_metric(score_fn=custom)
        result = metric(_gold("a"), _pred("b"))
        from dspy.teleprompt.gepa.gepa import ScoreWithFeedback

        assert isinstance(result, ScoreWithFeedback)
        assert result.score == 0.7
        assert result.feedback == "custom feedback"

    def test_accepts_gepa_extra_args(self) -> None:
        metric = build_gepa_feedback_metric()
        # GEPA passes pred_name and pred_trace
        result = metric(
            _gold("hello"),
            _pred("hello"),
            trace=None,
            pred_name="predict",
            pred_trace=None,
        )
        from dspy.teleprompt.gepa.gepa import ScoreWithFeedback

        assert isinstance(result, (float, ScoreWithFeedback))

    def test_respects_output_key_for_default_metric(self) -> None:
        metric = build_gepa_feedback_metric(output_key="summary")
        result = metric(
            SimpleNamespace(summary="hello"),
            SimpleNamespace(summary="hello"),
        )
        from dspy.teleprompt.gepa.gepa import ScoreWithFeedback

        assert isinstance(result, ScoreWithFeedback)
        assert result.score == 1.0

    def test_dspy_evaluate_accepts_gepa_feedback_metric(self) -> None:
        class Echo(dspy.Module):
            def forward(self, question: str):
                return dspy.Prediction(assistant_response=question)

        example = dspy.Example(
            question="hello",
            assistant_response="hello",
        ).with_inputs("question")
        result = dspy.Evaluate(
            devset=[example],
            metric=build_gepa_feedback_metric(),
        )(Echo())
        assert float(result) == 100.0
