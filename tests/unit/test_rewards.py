"""Tests for DSPy quality-constraint reward functions (dspy.Refine pattern)."""

from __future__ import annotations

import dspy
import pytest


# ── grounded_answer_reward ───────────────────────────────────────────


def test_grounded_answer_full_score():
    from fleet_rlm.runtime.models.rewards import grounded_answer_reward

    pred = dspy.Prediction(
        answer="The revenue was $20M in Q4.",
        citations=[{"source": "report.pdf"}],
        confidence=0.9,
    )
    assert grounded_answer_reward({}, pred) == pytest.approx(1.0)


def test_grounded_answer_empty():
    from fleet_rlm.runtime.models.rewards import grounded_answer_reward

    pred = dspy.Prediction(answer="", citations=[], confidence=0)
    assert grounded_answer_reward({}, pred) == pytest.approx(0.0)


def test_grounded_answer_no_citations():
    from fleet_rlm.runtime.models.rewards import grounded_answer_reward

    pred = dspy.Prediction(
        answer="A meaningful answer here.",
        citations=[],
        confidence=0.5,
    )
    assert grounded_answer_reward({}, pred) == pytest.approx(0.7)


# ── sub_rlm_answer_reward ───────────────────────────────────────────


def test_sub_rlm_good_answer():
    from fleet_rlm.runtime.models.rewards import sub_rlm_answer_reward

    pred = dspy.Prediction(answer="The answer is 42 because...")
    assert sub_rlm_answer_reward({}, pred) == 1.0


def test_sub_rlm_empty():
    from fleet_rlm.runtime.models.rewards import sub_rlm_answer_reward

    pred = dspy.Prediction(answer="")
    assert sub_rlm_answer_reward({}, pred) == 0.0


def test_sub_rlm_na():
    from fleet_rlm.runtime.models.rewards import sub_rlm_answer_reward

    pred = dspy.Prediction(answer="N/A")
    # "N/A" is < 5 chars, hits short-answer check first
    assert sub_rlm_answer_reward({}, pred) == pytest.approx(0.2)


def test_sub_rlm_short():
    from fleet_rlm.runtime.models.rewards import sub_rlm_answer_reward

    pred = dspy.Prediction(answer="Yes")
    assert sub_rlm_answer_reward({}, pred) == pytest.approx(0.2)


# ── variable_mode_answer_reward ──────────────────────────────────────


def test_variable_mode_good():
    from fleet_rlm.runtime.models.rewards import variable_mode_answer_reward

    pred = dspy.Prediction(answer="A well-formed answer with detail.")
    assert variable_mode_answer_reward({}, pred) == 1.0


def test_variable_mode_short():
    from fleet_rlm.runtime.models.rewards import variable_mode_answer_reward

    pred = dspy.Prediction(answer="Short")
    assert variable_mode_answer_reward({}, pred) == pytest.approx(0.3)


def test_variable_mode_empty():
    from fleet_rlm.runtime.models.rewards import variable_mode_answer_reward

    pred = dspy.Prediction(answer="")
    assert variable_mode_answer_reward({}, pred) == 0.0


# ── memory_action_reward ─────────────────────────────────────────────


def test_memory_action_full():
    from fleet_rlm.runtime.models.rewards import memory_action_reward

    pred = dspy.Prediction(
        action_type="write",
        risk_level="low",
        rationale="Need to save user preferences to persistent storage.",
    )
    assert memory_action_reward({}, pred) == pytest.approx(1.0)


def test_memory_action_invalid_action():
    from fleet_rlm.runtime.models.rewards import memory_action_reward

    pred = dspy.Prediction(
        action_type="invalid",
        risk_level="low",
        rationale="A somewhat longer explanation for the action.",
    )
    # Invalid action (0) + valid risk (0.3) + rationale >10 chars (0.2) = 0.5
    assert memory_action_reward({}, pred) == pytest.approx(0.5)


# ── refine_module wrapper ───────────────────────────────────────────


def test_refine_module_wraps():
    from unittest.mock import MagicMock, patch

    from fleet_rlm.runtime.models.rewards import refine_module

    mock_module = MagicMock(spec=dspy.Module)

    def fn(args: dict, pred: dspy.Prediction) -> float:
        return 1.0

    with patch("fleet_rlm.runtime.models.rewards.dspy.Refine") as mock_refine:
        mock_refine.return_value = MagicMock(spec=dspy.Module)
        refine_module(mock_module, fn, n=5, threshold=0.8)
        mock_refine.assert_called_once_with(
            mock_module, N=5, reward_fn=fn, threshold=0.8
        )
