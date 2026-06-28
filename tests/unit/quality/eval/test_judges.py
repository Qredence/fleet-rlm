"""Unit tests for LLM-as-judge scorers (VAL-C-025, VAL-C-026).

Tests verify:
- Judges use configured chat LM under BYOK, not hardcoded keys (VAL-C-025)
- Each judge returns a single float clamped to [0.0, 1.0] (VAL-C-026)
- Judge prompts instruct normalized 0.0-1.0 score (VAL-C-059)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fleet_rlm.quality.eval.judges import (
    JUDGE_NAMES,
    _extract_score,
    _load_prompt,
    answer_relevance,
    faithfulness_to_context,
    tool_selection_quality,
    trajectory_coherence,
)
from fleet_rlm.quality.eval.trace_record import TraceRecord


class TestExtractScore:
    """Tests for _extract_score clamping behavior (VAL-C-026)."""

    def test_pure_numeric_in_range(self):
        """Pure numeric string within range."""
        assert _extract_score("0.85") == 0.85

    def test_pure_numeric_zero(self):
        """Zero value."""
        assert _extract_score("0.0") == 0.0

    def test_pure_numeric_one(self):
        """One value."""
        assert _extract_score("1.0") == 1.0

    def test_pure_numeric_clamped_above(self):
        """Out-of-range value above 1.0 is clamped to 1.0."""
        assert _extract_score("1.5") == 1.0
        assert _extract_score("2.0") == 1.0
        assert _extract_score("10.0") == 1.0
        assert _extract_score("100") == 1.0

    def test_pure_numeric_clamped_below(self):
        """Out-of-range value below 0.0 is clamped to 0.0."""
        assert _extract_score("-0.5") == 0.0
        assert _extract_score("-1.0") == 0.0
        assert _extract_score("-10") == 0.0

    def test_json_score_in_range(self):
        """JSON response with score field."""
        assert _extract_score('{"score": 0.75}') == 0.75

    def test_json_score_clamped_above(self):
        """JSON response with out-of-range score is clamped."""
        assert _extract_score('{"score": 1.5}') == 1.0
        assert _extract_score('{"score": 2.0}') == 1.0

    def test_json_score_clamped_below(self):
        """JSON response with negative score is clamped."""
        assert _extract_score('{"score": -0.5}') == 0.0

    def test_score_with_explanation(self):
        """Score embedded in explanatory text."""
        assert _extract_score("Score: 0.85") == 0.85
        assert _extract_score("0.85 - good answer") == 0.85

    def test_empty_response(self):
        """Empty response returns 0.0."""
        assert _extract_score("") == 0.0
        assert _extract_score("   ") == 0.0

    def test_non_string_response(self):
        """Non-string response returns 0.0."""
        assert _extract_score(None) == 0.0  # type: ignore
        assert _extract_score(123) == 0.0  # type: ignore

    def test_no_numeric_content(self):
        """Response with no numeric content returns 0.0."""
        assert _extract_score("I cannot evaluate this") == 0.0
        assert _extract_score("no score here") == 0.0

    def test_integer_score(self):
        """Integer score is converted to float and clamped."""
        assert _extract_score("0") == 0.0
        assert _extract_score("1") == 1.0
        assert _extract_score("5") == 1.0  # clamped


class TestJudgePrompts:
    """Tests for judge prompt files (VAL-C-059)."""

    def test_all_prompt_files_exist(self):
        """All 4 judge prompt files exist."""
        for judge_name in JUDGE_NAMES:
            prompt = _load_prompt(judge_name)
            assert prompt is not None
            assert len(prompt) > 0

    def test_prompt_files_instruct_0_0_to_1_0_range(self):
        """Each prompt file instructs the judge to respond with a float in [0.0, 1.0]."""
        for judge_name in JUDGE_NAMES:
            prompt = _load_prompt(judge_name)
            # Check for explicit instruction about the score range
            assert "0.0" in prompt or "[0.0, 1.0]" in prompt, (
                f"Prompt for {judge_name} does not instruct 0.0-1.0 score range"
            )
            assert "1.0" in prompt, f"Prompt for {judge_name} does not mention 1.0 in scoring rubric"

    def test_prompt_files_are_non_empty(self):
        """Each prompt file has substantial content (>= 200 bytes per VAL-C-044)."""
        for judge_name in JUDGE_NAMES:
            prompt = _load_prompt(judge_name)
            assert len(prompt) >= 200, f"Prompt for {judge_name} is too short ({len(prompt)} bytes)"


class TestJudgeBYOK:
    """Tests for BYOK compliance (VAL-C-025)."""

    def test_judges_module_no_hardcoded_keys(self):
        """judges.py does not contain hardcoded API keys."""
        judges_path = (
            Path(__file__).parent.parent.parent.parent / "src" / "fleet_rlm" / "quality" / "eval" / "judges.py"
        )
        if judges_path.exists():
            content = judges_path.read_text()
            # Check for common hardcoded key patterns
            assert "OPENAI_API_KEY" not in content or "os.environ" in content, (
                "judges.py contains hardcoded OPENAI_API_KEY"
            )
            assert "GEMINI_API_KEY" not in content or "os.environ" in content, (
                "judges.py contains hardcoded GEMINI_API_KEY"
            )
            # Check that no literal API key strings are present
            assert "sk-" not in content, "judges.py contains literal OpenAI API key"
            assert "AIza" not in content, "judges.py contains literal Gemini API key"

    def test_judge_accepts_lm_parameter(self):
        """Each judge function accepts an lm parameter."""
        # Create a minimal trace record for testing
        trace = TraceRecord(
            trace_id="test-trace",
            route="rlm",
            user_request="test request",
            core_memory="",
            history=[],
            active_skills=[],
            context="",
            trajectory_spans=[],
            final_answer="test answer",
            timeouts={},
            trace_outputs={},
            metadata={},
            token_cost=0,
            latency_s=0.0,
            parent_span_id=None,
        )

        # Create a mock LM that returns a valid score
        mock_lm = MagicMock()
        mock_lm.return_value = ["0.85"]

        # Each judge should accept the lm parameter and return a float
        for judge_fn in [answer_relevance, faithfulness_to_context, trajectory_coherence, tool_selection_quality]:
            score = judge_fn(trace, mock_lm)
            assert isinstance(score, float)
            assert 0.0 <= score <= 1.0


class TestJudgeClamping:
    """Tests for judge score clamping (VAL-C-026)."""

    def _make_trace(self) -> TraceRecord:
        """Create a minimal trace record for testing."""
        return TraceRecord(
            trace_id="test-trace",
            route="rlm",
            user_request="test request",
            core_memory="",
            history=[],
            active_skills=[],
            context="",
            trajectory_spans=[],
            final_answer="test answer",
            timeouts={},
            trace_outputs={},
            metadata={},
            token_cost=0,
            latency_s=0.0,
            parent_span_id=None,
        )

    def test_judge_clamps_high_score(self):
        """Judge clamps out-of-range high scores to 1.0."""
        trace = self._make_trace()
        mock_lm = MagicMock()
        mock_lm.return_value = ["1.5"]  # Out of range

        score = answer_relevance(trace, mock_lm)
        assert score == 1.0

    def test_judge_clamps_low_score(self):
        """Judge clamps out-of-range low scores to 0.0."""
        trace = self._make_trace()
        mock_lm = MagicMock()
        mock_lm.return_value = ["-0.5"]  # Out of range

        score = answer_relevance(trace, mock_lm)
        assert score == 0.0

    def test_judge_handles_none_lm(self):
        """Judge returns None (skips LLM call) when no LM is configured."""
        trace = self._make_trace()

        score = answer_relevance(trace, None)
        assert score is None

    def test_judge_handles_lm_exception(self):
        """Judge returns 0.0 when LM raises an exception."""
        trace = self._make_trace()
        mock_lm = MagicMock()
        mock_lm.side_effect = Exception("LM error")

        score = answer_relevance(trace, mock_lm)
        assert score == 0.0

    def test_judge_handles_malformed_response(self):
        """Judge returns 0.0 for malformed LM responses."""
        trace = self._make_trace()
        mock_lm = MagicMock()
        mock_lm.return_value = ["I cannot evaluate this response"]

        score = answer_relevance(trace, mock_lm)
        assert score == 0.0

    def test_all_judges_return_float_in_range(self):
        """All 4 judges return floats in [0.0, 1.0] for valid responses."""
        trace = self._make_trace()
        mock_lm = MagicMock()

        for expected_score in [0.0, 0.25, 0.5, 0.75, 1.0]:
            mock_lm.return_value = [str(expected_score)]

            for judge_fn in [answer_relevance, faithfulness_to_context, trajectory_coherence, tool_selection_quality]:
                score = judge_fn(trace, mock_lm)
                assert isinstance(score, float), f"{judge_fn.__name__} did not return float"
                assert 0.0 <= score <= 1.0, f"{judge_fn.__name__} returned {score} outside [0.0, 1.0]"
                assert score == expected_score
