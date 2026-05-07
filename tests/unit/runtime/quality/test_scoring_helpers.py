"""Tests for runtime/quality/scoring_helpers.py shared scoring primitives."""

from __future__ import annotations

import pytest

from fleet_rlm.quality.scoring_helpers import (
    ScoreFeedbackBuilder,
    action_match_score,
    boundedness_score,
    set_overlap_score,
    text_presence_score,
)


@pytest.mark.parametrize(
    ("expected", "actual", "expected_score"),
    [
        ({"a", "b"}, {"a", "b"}, 1.0),
        ({"a", "b", "c"}, {"a", "c"}, 2.0 / 3.0),
        ({"a"}, {"b"}, 0.0),
        (set(), {"a"}, 1.0),
    ],
)
def test_set_overlap_score(expected, actual, expected_score) -> None:
    score = set_overlap_score(expected, actual)
    if isinstance(expected_score, float) and expected_score not in (0.0, 1.0):
        assert abs(score - expected_score) < 1e-9
    else:
        assert score == expected_score


@pytest.mark.parametrize(
    ("text", "expected_score"),
    [
        ("hello", 1.0),
        ("", 0.0),
        ("   ", 0.0),
    ],
)
def test_text_presence_score(text, expected_score) -> None:
    assert text_presence_score(text) == expected_score


@pytest.mark.parametrize(
    ("value", "budget", "expected_score"),
    [
        (3, 5, 1.0),
        (6, 5, 0.0),
        (5, 5, 1.0),
        (0, 0, 1.0),
    ],
)
def test_boundedness_score(value, budget, expected_score) -> None:
    assert boundedness_score(value, budget) == expected_score


@pytest.mark.parametrize(
    ("expected", "actual", "expected_score"),
    [
        ("recurse", "recurse", 1.0),
        ("recurse", "finalize", 0.0),
    ],
)
def test_action_match_score(expected, actual, expected_score) -> None:
    assert action_match_score(expected, actual) == expected_score


@pytest.mark.parametrize(
    ("entries", "expected_score", "expected_feedback_substring"),
    [
        ([(1.0, 0.5, "Half score.")], 0.5, "Half score."),
        ([(0.6, 1.0, "Full."), (0.4, 0.0, "Zero.")], 0.6, None),
        ([(2.0, 1.0, "Over weight.")], 1.0, None),
        ([], 0.0, ""),
    ],
)
def test_score_feedback_builder(entries, expected_score, expected_feedback_substring) -> None:
    builder = ScoreFeedbackBuilder()
    for weight, score, feedback in entries:
        builder.add(weight, score, feedback)
    result = builder.build()
    assert result.score == pytest.approx(expected_score)
    if expected_feedback_substring is not None:
        assert expected_feedback_substring in result.feedback
