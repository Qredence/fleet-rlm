from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import pytest

from fleet_rlm.quality.scoring_helpers import (
    ScoreFeedbackBuilder,
    action_match_score,
    boundedness_score,
    set_overlap_score,
    text_presence_score,
)


@dataclass
class _FakeScoreWithFeedback:
    score: float
    feedback: str


def _install_fake_dspy(monkeypatch: pytest.MonkeyPatch) -> None:
    dspy_module = types.ModuleType("dspy")
    teleprompt_module = types.ModuleType("dspy.teleprompt")
    gepa_module = types.ModuleType("dspy.teleprompt.gepa")
    gepa_utils_module = types.ModuleType("dspy.teleprompt.gepa.gepa_utils")
    gepa_utils_module.ScoreWithFeedback = _FakeScoreWithFeedback

    monkeypatch.setitem(sys.modules, "dspy", dspy_module)
    monkeypatch.setitem(sys.modules, "dspy.teleprompt", teleprompt_module)
    monkeypatch.setitem(sys.modules, "dspy.teleprompt.gepa", gepa_module)
    monkeypatch.setitem(sys.modules, "dspy.teleprompt.gepa.gepa_utils", gepa_utils_module)


@pytest.mark.parametrize(
    ("expected", "actual", "score"),
    [
        ({"a", "b"}, {"a", "b", "c"}, 1.0),
        ({"a", "b", "c"}, {"a", "c"}, 2 / 3),
        (set(), {"anything"}, 1.0),
    ],
)
def test_score_helpers_return_expected_scores(expected, actual, score) -> None:
    assert set_overlap_score(expected, actual) == pytest.approx(score)
    assert text_presence_score("has text") == 1.0
    assert text_presence_score("   ") == 0.0
    assert boundedness_score(3, 3) == 1.0
    assert boundedness_score(4, 3) == 0.0
    assert action_match_score("Finalize", " finalize ") == 1.0


def test_score_feedback_builder_builds_clamped_weighted_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_dspy(monkeypatch)
    builder = ScoreFeedbackBuilder()

    result = builder.add(0.7, 1.0, "Match.").add(0.7, 1.0, "Still good.").build()

    assert result == _FakeScoreWithFeedback(score=1.0, feedback="Match. Still good.")
    assert builder.raw_score == 1.0
