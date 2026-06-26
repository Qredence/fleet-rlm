"""Unit tests for judges module."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from fleet_rlm.quality.eval.judges import (
    _extract_score,
    _load_prompt,
    answer_relevance,
    faithfulness_to_context,
    tool_selection_quality,
    trajectory_coherence,
)
from fleet_rlm.quality.eval.trace_record import TraceRecord


def make_trace(**kwargs) -> TraceRecord:
    """Helper to create a TraceRecord with defaults."""
    defaults = {
        "trace_id": "test-trace",
        "route": "rlm",
        "user_request": "test request",
        "core_memory": "test memory",
        "history": [],
        "active_skills": [],
        "context": "test context",
        "trajectory_spans": [],
        "final_answer": "test answer",
        "timeouts": {},
        "trace_outputs": {},
        "metadata": {},
        "token_cost": 0,
        "latency_s": 0.0,
        "parent_span_id": None,
    }
    defaults.update(kwargs)
    return TraceRecord(**defaults)


class TestPromptLoading:
    """Tests for prompt loading from disk."""

    def test_loads_all_4_prompts(self) -> None:
        """Test that all 4 judge prompts can be loaded."""
        prompts = [
            "answer_relevance",
            "faithfulness_to_context",
            "trajectory_coherence",
            "tool_selection_quality",
        ]
        for prompt_name in prompts:
            prompt = _load_prompt(prompt_name)
            assert len(prompt) > 0
            assert "[0.0, 1.0]" in prompt

    def test_prompt_file_not_found_raises(self) -> None:
        """Test that missing prompt file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            _load_prompt("nonexistent_prompt")


class TestScoreExtraction:
    """Tests for score extraction from LLM responses."""

    def test_extracts_pure_numeric(self) -> None:
        """Test extraction of pure numeric response."""
        assert _extract_score("0.85") == 0.85

    def test_extracts_from_json(self) -> None:
        """Test extraction from JSON response."""
        assert _extract_score('{"score": 0.9}') == 0.9

    def test_clamps_to_valid_range(self) -> None:
        """Test that scores are clamped to [0.0, 1.0]."""
        assert _extract_score("1.5") == 1.0
        assert _extract_score("-0.5") == 0.0

    def test_returns_0_for_unparseable(self) -> None:
        """Test that unparseable responses return 0.0."""
        assert _extract_score("I cannot provide a score") == 0.0

    def test_extracts_from_text_with_context(self) -> None:
        """Test extraction from text with surrounding context."""
        assert _extract_score("The score is 0.75 out of 1.0") == 0.75


class TestJudgeFunctions:
    """Tests for judge functions."""

    def test_judge_returns_float_in_range(self) -> None:
        """Test that judges return float in [0.0, 1.0]."""
        trace = make_trace()
        mock_lm = Mock(return_value="0.8")

        score = answer_relevance(trace, mock_lm)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_judge_handles_lm_error(self) -> None:
        """Test that judges handle LM errors gracefully."""
        trace = make_trace()
        mock_lm = Mock(side_effect=Exception("LM error"))

        score = answer_relevance(trace, mock_lm)
        assert score == 0.0  # Should return 0.0 on error

    def test_all_judges_callable(self) -> None:
        """Test that all judge functions are callable."""
        trace = make_trace()
        mock_lm = Mock(return_value="0.7")

        judges = [
            answer_relevance,
            faithfulness_to_context,
            trajectory_coherence,
            tool_selection_quality,
        ]

        for judge in judges:
            score = judge(trace, mock_lm)
            assert isinstance(score, float)
            assert 0.0 <= score <= 1.0
