"""Tests for ``fleet_rlm.integrations.observability.workspace_metrics``."""

from __future__ import annotations

from types import SimpleNamespace

from fleet_rlm.integrations.observability.workspace_metrics import (
    completeness_feedback_metric,
    exact_match_feedback_metric,
    workspace_feedback_metric,
)


def _gold(text: str) -> SimpleNamespace:
    return SimpleNamespace(assistant_response=text)


def _pred(text: str) -> SimpleNamespace:
    return SimpleNamespace(assistant_response=text)


class TestExactMatchFeedback:
    def test_exact_match(self) -> None:
        score, feedback = exact_match_feedback_metric(_gold("hello"), _pred("hello"))
        assert score == 1.0
        assert "Exact match" in feedback

    def test_case_mismatch(self) -> None:
        score, feedback = exact_match_feedback_metric(_gold("Hello"), _pred("hello"))
        assert score == 0.8
        assert "casing" in feedback.lower()

    def test_substring_match(self) -> None:
        score, feedback = exact_match_feedback_metric(
            _gold("hello"), _pred("hello world")
        )
        assert score == 0.6
        assert "substring" in feedback.lower()

    def test_empty_prediction(self) -> None:
        score, feedback = exact_match_feedback_metric(_gold("hello"), _pred(""))
        assert score == 0.0
        assert "Empty response" in feedback

    def test_no_expected(self) -> None:
        score, feedback = exact_match_feedback_metric(_gold(""), _pred("hello"))
        assert score == 0.0
        assert "No expected" in feedback

    def test_full_mismatch(self) -> None:
        score, feedback = exact_match_feedback_metric(_gold("hello"), _pred("goodbye"))
        assert score == 0.0
        assert "Mismatch" in feedback


class TestCompletenessFeedback:
    def test_full_coverage(self) -> None:
        text = "machine learning optimization pipeline"
        score, feedback = completeness_feedback_metric(_gold(text), _pred(text))
        assert score == 1.0
        assert "coverage" in feedback.lower()

    def test_partial_coverage(self) -> None:
        score, feedback = completeness_feedback_metric(
            _gold("machine learning optimization pipeline"),
            _pred("machine learning is great"),
        )
        assert 0.0 < score < 1.0

    def test_empty_prediction(self) -> None:
        score, feedback = completeness_feedback_metric(_gold("hello world"), _pred(""))
        assert score == 0.0


class TestWorkspaceFeedbackMetric:
    def test_perfect_score(self) -> None:
        score, feedback = workspace_feedback_metric(_gold("hello"), _pred("hello"))
        assert score == 1.0
        assert "exact_match=1.00" in feedback

    def test_blended_score(self) -> None:
        score, feedback = workspace_feedback_metric(
            _gold("hello world"), _pred("goodbye world")
        )
        assert 0.0 < score < 1.0
        assert "exact_match=" in feedback
        assert "completeness=" in feedback

    def test_returns_tuple(self) -> None:
        result = workspace_feedback_metric(_gold("a"), _pred("b"))
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], float)
        assert isinstance(result[1], str)
