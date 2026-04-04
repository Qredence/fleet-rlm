"""Integration tests verifying true-RLM fidelity (Algorithm 1, arXiv 2512.24601v2).

These tests verify the three core properties of a true RLM:
1. Prompt as REPL variable — never enters LLM context window
2. Symbolic output via SUBMIT — unbounded output length
3. sub_rlm() callable from inside REPL code — Ω(|P|) sub-calls

All tests use mocked interpreters and LM to avoid real API calls.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import dspy
import pytest


# ── Helpers ──────────────────────────────────────────────────────────


def _mock_interpreter(sub_rlm_fn: Any = None) -> MagicMock:
    """Create a mock interpreter that records execute() calls."""
    interp = MagicMock()
    interp._started = True
    interp.sub_lm = None
    interp.sub_rlm = sub_rlm_fn or MagicMock(return_value="child answer")
    interp.sub_rlm_batched = MagicMock(return_value=["batch1", "batch2"])
    interp.execute.return_value = "stdout output"
    return interp


# ── Property 1: Prompt as REPL variable ─────────────────────────────


class TestPromptAsVariable:
    """Verify that dspy.RLM stores prompt in REPL, not in LLM context."""

    def test_signature_has_prompt_field(self):
        from fleet_rlm.runtime.agent.signatures import RLMVariableSignature

        assert "prompt" in RLMVariableSignature.input_fields
        desc = RLMVariableSignature.input_fields["prompt"].json_schema_extra.get(
            "desc", ""
        )
        assert "REPL variable" in desc

    def test_module_passes_prompt_to_inner_rlm(self):
        """RLMVariableExecutionModule.forward passes prompt to dspy.RLM."""
        from fleet_rlm.runtime.models.builders import RLMVariableExecutionModule

        interp = _mock_interpreter()
        mock_rlm = MagicMock()
        mock_rlm.return_value = dspy.Prediction(answer="result")

        with patch(
            "fleet_rlm.runtime.models.builders.create_runtime_rlm",
            return_value=mock_rlm,
        ):
            module = RLMVariableExecutionModule(interpreter=interp)
            result = module(task="count words", prompt="hello " * 100000)

        # Verify prompt was passed (dspy.RLM will store it as variable)
        mock_rlm.assert_called_once_with(task="count words", prompt="hello " * 100000)
        assert result.answer == "result"

    def test_variable_mode_threshold(self):
        from fleet_rlm.runtime.models.builders import VARIABLE_MODE_THRESHOLD

        assert VARIABLE_MODE_THRESHOLD == 32_000

    def test_variable_mode_output_chars_limited(self):
        from fleet_rlm.runtime.models.builders import VARIABLE_MODE_MAX_OUTPUT_CHARS

        # Should be much smaller than default 100K to force variable usage
        assert VARIABLE_MODE_MAX_OUTPUT_CHARS <= 10_000


# ── Property 2: Symbolic output ─────────────────────────────────────


class TestSymbolicOutput:
    """Verify output via SUBMIT/Final, not autoregressive generation."""

    def test_signature_mentions_submit(self):
        from fleet_rlm.runtime.agent.signatures import RLMVariableSignature

        desc = RLMVariableSignature.output_fields["answer"].json_schema_extra.get(
            "desc", ""
        )
        assert "SUBMIT" in desc

    def test_metadata_summary_truncates_large_output(self):
        from fleet_rlm.runtime.tools.llm_tools import metadata_summary

        large = "x" * 50000
        result = metadata_summary(large, preview_length=200, label="Result")
        assert "[Result: 50,000 chars]" in result
        assert len(result) < 300

    def test_metadata_summary_passes_small_output(self):
        from fleet_rlm.runtime.tools.llm_tools import metadata_summary

        small = "hello world"
        assert metadata_summary(small) == "hello world"


# ── Property 3: sub_rlm() callable from REPL code ──────────────────


class TestSubRLMRecursion:
    """Verify sub_rlm is available as a tool inside dspy.RLM."""

    def test_sub_rlm_registered_as_tool(self):
        """RLMVariableExecutionModule includes sub_rlm in tools."""
        from fleet_rlm.runtime.models.builders import RLMVariableExecutionModule

        interp = _mock_interpreter()

        with patch(
            "fleet_rlm.runtime.models.builders.create_runtime_rlm"
        ) as mock_create:
            mock_create.return_value = MagicMock(spec=dspy.Module)
            RLMVariableExecutionModule(interpreter=interp)
            call_kwargs = mock_create.call_args[1]
            tools = call_kwargs.get("tools", [])
            assert len(tools) == 2  # sub_rlm + sub_rlm_batched

    def test_sub_rlm_depth_enforcement(self):
        """sub_rlm refuses at max depth."""
        from fleet_rlm.runtime.tools.llm_tools import LLMQueryMixin

        mixin = LLMQueryMixin()
        mixin._sub_rlm_depth = 2
        mixin._sub_rlm_max_depth = 2
        mixin._max_llm_calls = 10
        mixin._llm_call_count = 0
        mixin._llm_call_lock = MagicMock()

        with pytest.raises(RuntimeError, match="[Dd]epth"):
            mixin.sub_rlm("test prompt")

    def test_sub_rlm_budget_enforcement(self):
        """sub_rlm refuses when budget exhausted."""
        from fleet_rlm.runtime.tools.llm_tools import LLMQueryMixin

        mixin = LLMQueryMixin()
        mixin._sub_rlm_depth = 0
        mixin._sub_rlm_max_depth = 2
        mixin.max_llm_calls = 5
        mixin._llm_call_count = 5
        mixin._llm_call_lock = MagicMock()

        with pytest.raises(RuntimeError, match="[Bb]udget"):
            mixin.sub_rlm("test prompt")

    def test_sub_rlm_in_bridge_tools(self):
        """bridge_tools() includes sub_rlm and sub_rlm_batched."""
        from fleet_rlm.integrations.providers.daytona.interpreter_execution import (
            bridge_tools,
        )

        mock_interp = MagicMock()
        mock_interp.sub_rlm = MagicMock(return_value="answer")
        mock_interp.sub_rlm_batched = MagicMock(return_value=["a", "b"])
        mock_interp.llm_query = MagicMock(return_value="answer")
        mock_interp.llm_query_batched = MagicMock(return_value=["a"])

        tools = bridge_tools(mock_interp)
        assert "sub_rlm" in tools
        assert "sub_rlm_batched" in tools

    def test_rlm_query_still_blocked_in_sandbox(self):
        """rlm_query/rlm_query_batched remain blocked for sandbox code."""
        from fleet_rlm.integrations.providers.daytona.interpreter_execution import (
            bridge_tools,
        )

        mock_interp = MagicMock()
        mock_interp.sub_rlm = MagicMock(return_value="answer")
        mock_interp.sub_rlm_batched = MagicMock(return_value=["a", "b"])
        mock_interp.llm_query = MagicMock(return_value="answer")
        mock_interp.llm_query_batched = MagicMock(return_value=["a"])

        tools = bridge_tools(mock_interp)
        # rlm_query is agent-level (ReAct), NOT REPL-level
        assert "rlm_query" not in tools
        assert "rlm_query_batched" not in tools


# ── Auto-routing ─────────────────────────────────────────────────────


class TestAutoRouting:
    """Verify long prompts auto-route to variable-mode RLM."""

    def test_has_interpreter_true(self):
        from fleet_rlm.runtime.tools.sandbox_delegate_tools import _has_interpreter

        agent = MagicMock()
        agent.interpreter._started = True
        assert _has_interpreter(agent) is True

    def test_has_interpreter_false_no_interpreter(self):
        from fleet_rlm.runtime.tools.sandbox_delegate_tools import _has_interpreter

        agent = MagicMock()
        agent.interpreter = None
        assert _has_interpreter(agent) is False

    def test_registry_long_doc_modules_variable_mode(self):
        from fleet_rlm.runtime.models.registry import RUNTIME_MODULE_REGISTRY

        variable_mode_names = {
            name for name, defn in RUNTIME_MODULE_REGISTRY.items() if defn.variable_mode
        }
        assert variable_mode_names == {
            "summarize_long_document",
            "extract_from_logs",
        }


# ── Long-context simulation ─────────────────────────────────────────


class TestLongContext:
    """Simulate long-context processing (no real LLM calls)."""

    def test_million_char_prompt_accepted(self):
        """1M char prompt is accepted by RLMVariableExecutionModule."""
        from fleet_rlm.runtime.models.builders import RLMVariableExecutionModule

        interp = _mock_interpreter()
        mock_rlm = MagicMock()
        mock_rlm.return_value = dspy.Prediction(answer="processed 1M chars")

        with patch(
            "fleet_rlm.runtime.models.builders.create_runtime_rlm",
            return_value=mock_rlm,
        ):
            module = RLMVariableExecutionModule(interpreter=interp)
            prompt = "data:" + "x" * 1_000_000
            result = module(task="summarize", prompt=prompt)

        assert result.answer == "processed 1M chars"
        # Verify the full prompt was passed (not truncated)
        call_args = mock_rlm.call_args[1]
        assert len(call_args["prompt"]) == 1_000_005

    def test_variable_mode_with_extra_tools(self):
        """Extra tools are passed through to dspy.RLM."""
        from fleet_rlm.runtime.models.builders import RLMVariableExecutionModule

        interp = _mock_interpreter()

        def custom_tool(x: str) -> str:
            return x.upper()

        with patch(
            "fleet_rlm.runtime.models.builders.create_runtime_rlm"
        ) as mock_create:
            mock_create.return_value = MagicMock(spec=dspy.Module)
            RLMVariableExecutionModule(interpreter=interp, extra_tools=[custom_tool])
            call_kwargs = mock_create.call_args[1]
            tools = call_kwargs.get("tools", [])
            # custom_tool + sub_rlm + sub_rlm_batched
            assert len(tools) == 3


# ── Reward function quality gates ────────────────────────────────────


class TestQualityGates:
    """Verify reward functions correctly score predictions."""

    def test_grounded_perfect_score(self):
        from fleet_rlm.runtime.models.rewards import grounded_answer_reward

        pred = dspy.Prediction(
            answer="Complete answer with details.",
            citations=[{"src": "doc1"}, {"src": "doc2"}],
            confidence=0.95,
        )
        assert grounded_answer_reward({}, pred) == pytest.approx(1.0)

    def test_grounded_zero_score(self):
        from fleet_rlm.runtime.models.rewards import grounded_answer_reward

        pred = dspy.Prediction(answer="", citations=[], confidence=0)
        assert grounded_answer_reward({}, pred) == pytest.approx(0.0)

    def test_variable_mode_meaningful(self):
        from fleet_rlm.runtime.models.rewards import variable_mode_answer_reward

        pred = dspy.Prediction(answer="A meaningful multi-word answer here.")
        assert variable_mode_answer_reward({}, pred) == 1.0

    def test_variable_mode_empty(self):
        from fleet_rlm.runtime.models.rewards import variable_mode_answer_reward

        pred = dspy.Prediction(answer="")
        assert variable_mode_answer_reward({}, pred) == 0.0
