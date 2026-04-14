"""Tests for runtime/quality/scoring_helpers.py shared scoring primitives."""

from __future__ import annotations

import pytest

from fleet_rlm.runtime.quality.scoring_helpers import (
    ScoreFeedbackBuilder,
    action_match_score,
    boundedness_score,
    set_overlap_score,
    text_presence_score,
)


# -- set_overlap_score --------------------------------------------------------


def test_set_overlap_full_match() -> None:
    assert set_overlap_score({"a", "b"}, {"a", "b"}) == 1.0


def test_set_overlap_partial() -> None:
    score = set_overlap_score({"a", "b", "c"}, {"a", "c"})
    assert abs(score - 2.0 / 3.0) < 1e-9


def test_set_overlap_no_match() -> None:
    assert set_overlap_score({"a"}, {"b"}) == 0.0


def test_set_overlap_empty_expected() -> None:
    assert set_overlap_score(set(), {"a"}) == 1.0


# -- text_presence_score ------------------------------------------------------


def test_text_presence_nonempty() -> None:
    assert text_presence_score("hello") == 1.0


def test_text_presence_empty() -> None:
    assert text_presence_score("") == 0.0


def test_text_presence_whitespace_only() -> None:
    assert text_presence_score("   ") == 0.0


# -- boundedness_score --------------------------------------------------------


def test_boundedness_within_budget() -> None:
    assert boundedness_score(3, 5) == 1.0


def test_boundedness_exceeds_budget() -> None:
    assert boundedness_score(6, 5) == 0.0


def test_boundedness_exact_budget() -> None:
    assert boundedness_score(5, 5) == 1.0


def test_boundedness_zero_budget() -> None:
    assert boundedness_score(0, 0) == 1.0


# -- action_match_score -------------------------------------------------------


def test_action_match_exact() -> None:
    assert action_match_score("recurse", "recurse") == 1.0


def test_action_match_mismatch() -> None:
    assert action_match_score("recurse", "finalize") == 0.0


# -- ScoreFeedbackBuilder -----------------------------------------------------


def test_builder_single_entry() -> None:
    builder = ScoreFeedbackBuilder()
    builder.add(1.0, 0.5, "Half score.")
    result = builder.build()
    assert result.score == pytest.approx(0.5)
    assert "Half score." in result.feedback


def test_builder_weighted_combination() -> None:
    builder = ScoreFeedbackBuilder()
    builder.add(0.6, 1.0, "Full.")
    builder.add(0.4, 0.0, "Zero.")
    result = builder.build()
    assert result.score == pytest.approx(0.6)


def test_builder_clamps_to_01() -> None:
    builder = ScoreFeedbackBuilder()
    builder.add(2.0, 1.0, "Over weight.")
    result = builder.build()
    assert result.score <= 1.0


def test_builder_empty_returns_zero() -> None:
    builder = ScoreFeedbackBuilder()
    result = builder.build()
    assert result.score == 0.0
    assert result.feedback == ""
