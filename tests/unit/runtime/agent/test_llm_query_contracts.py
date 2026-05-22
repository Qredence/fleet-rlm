"""VAL-RLM contract tests for LLM query mixin and sandbox bridge callbacks.

Covers:
- VAL-RLM-004: sub_rlm_batched enforces configured sibling concurrency
- VAL-RLM-019: Sandbox recursive callback failures are JSON-safe and parent-preserving
- VAL-RLM-020: llm_query_batched preserves order and debits budget exactly once per prompt
"""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from fleet_rlm.runtime.execution.interpreter_support import (
    initialize_llm_query_state,
    initialize_sub_rlm_state,
)
from fleet_rlm.runtime.execution.llm_query import LLMQueryMixin

# ---------------------------------------------------------------------------
# Minimal interpreter stub with LLMQueryMixin
# ---------------------------------------------------------------------------


class _StubInterpreter(LLMQueryMixin):
    def __init__(
        self,
        *,
        max_llm_calls: int = 50,
        llm_call_timeout: int = 60,
        depth: int = 0,
        max_depth: int = 3,
    ) -> None:
        initialize_llm_query_state(
            self,
            sub_lm=None,
            max_llm_calls=max_llm_calls,
            llm_call_timeout=llm_call_timeout,
        )
        initialize_sub_rlm_state(self, depth=depth, max_depth=max_depth)
        self.child_budgets: list[int] = []

    def build_delegate_child(self, *, remaining_llm_budget: int) -> "_StubInterpreter":
        self.child_budgets.append(remaining_llm_budget)
        child = _StubInterpreter(
            max_llm_calls=remaining_llm_budget,
            depth=self._sub_rlm_depth + 1,
            max_depth=self._sub_rlm_max_depth,
        )
        child._check_and_increment_llm_calls = self._check_and_increment_llm_calls
        child._remaining_llm_budget = self._remaining_llm_budget
        return child

    def start(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


# ---------------------------------------------------------------------------
# VAL-RLM-004: sub_rlm_batched sibling concurrency is bounded
# ---------------------------------------------------------------------------


class TestSiblingConcurrencyBound:
    """VAL-RLM-004: At most max_workers sibling children run concurrently."""

    def test_sub_rlm_batched_concurrent_siblings_are_bounded(self) -> None:
        """At most max_workers=4 sub_rlm children run concurrently."""
        from fleet_rlm.runtime.execution import llm_query as llm_query_mod

        max_concurrency = 4  # _SUB_RLM_BATCH_EXECUTOR max_workers
        concurrent_count = 0
        max_concurrent_seen = 0
        lock = threading.Lock()
        # All 5 children meet at the barrier; if concurrency > max_workers, they deadlock
        results_gate = threading.Barrier(max_concurrency, timeout=3)

        interp = _StubInterpreter(max_llm_calls=10, max_depth=3)

        def _fake_build(**kwargs: Any) -> MagicMock:
            nonlocal concurrent_count, max_concurrent_seen
            _ = kwargs["interpreter"]

            def _module(*, prompt: str, context: str) -> Any:
                nonlocal concurrent_count, max_concurrent_seen
                with lock:
                    concurrent_count += 1
                    max_concurrent_seen = max(max_concurrent_seen, concurrent_count)
                try:
                    results_gate.wait(timeout=5)
                finally:
                    with lock:
                        concurrent_count -= 1
                pred = MagicMock()
                pred.answer = f"answer-{prompt}"
                return pred

            return MagicMock(side_effect=_module)

        with patch.object(llm_query_mod, "build_recursive_subquery_rlm", side_effect=_fake_build):
            with pytest.raises(RuntimeError):
                # 5 tasks but max 4 can be in the barrier at once — the 5th queues
                interp.sub_rlm_batched(["a", "b", "c", "d", "e"])

        # max_concurrent_seen should not exceed the executor's max_workers
        assert max_concurrent_seen <= max_concurrency, (
            f"Concurrency {max_concurrent_seen} exceeded limit {max_concurrency}"
        )

    def test_sub_rlm_batched_does_not_reject_over_limit_inputs(self) -> None:
        """Inputs beyond concurrency limit are queued, not rejected."""
        interp = _StubInterpreter(max_llm_calls=20, max_depth=3)

        call_count = 0

        def _fake_build(**kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            pred = MagicMock()
            pred.answer = f"answer-{call_count}"
            return MagicMock(return_value=pred)

        with patch("fleet_rlm.runtime.execution.llm_query.build_recursive_subquery_rlm", side_effect=_fake_build):
            # Submit 6 tasks — should all succeed, not just first 4
            results = interp.sub_rlm_batched(["a", "b", "c", "d", "e", "f"])

        assert len(results) == 6, "All 6 inputs must be processed"


# ---------------------------------------------------------------------------
# VAL-RLM-019: Sandbox recursive callback failures are JSON-safe
# ---------------------------------------------------------------------------


class TestBridgeCallbackFailuresSafe:
    """VAL-RLM-019: invoke_tool wraps exceptions as JSON-safe structured errors."""

    def test_depth_exhaustion_returns_json_safe_error(self) -> None:
        """Bridge invoke_tool converts sub_rlm depth RuntimeError to JSON-safe dict."""
        from fleet_rlm.integrations.daytona.bridge_callbacks import invoke_tool

        interpreter = _StubInterpreter(depth=3, max_depth=3)
        # sub_rlm will raise RuntimeError for depth exhaustion
        result = invoke_tool(interpreter, "sub_rlm", ["hello"], {})

        assert isinstance(result, dict), "invoke_tool must return a dict on failure"
        assert result.get("status") == "error"
        assert "reason" in result
        assert "error" in result
        assert "traceback" not in str(result), "No raw traceback in JSON-safe result"

    def test_budget_exhaustion_returns_json_safe_error(self) -> None:
        """Bridge invoke_tool converts sub_rlm budget RuntimeError to JSON-safe dict."""
        from fleet_rlm.integrations.daytona.bridge_callbacks import invoke_tool

        interpreter = _StubInterpreter(max_llm_calls=5)
        interpreter._llm_call_count = 5  # Exhaust budget

        result = invoke_tool(interpreter, "sub_rlm", ["task"], {})

        assert isinstance(result, dict)
        assert result.get("status") == "error"
        assert "error" in result

    def test_null_answer_returns_json_safe_error(self) -> None:
        """Bridge invoke_tool wraps RuntimeError from null-answer sub_rlm."""
        from fleet_rlm.integrations.daytona.bridge_callbacks import invoke_tool

        interpreter = _StubInterpreter(max_llm_calls=50)
        pred = MagicMock()
        pred.answer = None  # null answer

        with patch("fleet_rlm.runtime.execution.llm_query.build_recursive_subquery_rlm") as mock_build:
            mock_build.return_value = MagicMock(return_value=pred)
            result = invoke_tool(interpreter, "sub_rlm", ["no-submit"], {})

        assert isinstance(result, dict)
        assert result.get("status") == "error"
        assert "error" in result

    def test_child_exception_returns_json_safe_error(self) -> None:
        """Bridge invoke_tool wraps unexpected child exceptions as JSON-safe errors."""
        from fleet_rlm.integrations.daytona.bridge_callbacks import invoke_tool

        interpreter = _StubInterpreter(max_llm_calls=50)

        def _raising_rlm(**kwargs: Any) -> None:
            raise ValueError("unexpected child error")

        with patch("fleet_rlm.runtime.execution.llm_query.build_recursive_subquery_rlm") as mock_build:
            mock_build.return_value = MagicMock(side_effect=_raising_rlm)
            result = invoke_tool(interpreter, "sub_rlm", ["bad-task"], {})

        assert isinstance(result, dict)
        assert result.get("status") == "error"
        assert "tool_name" in result
        assert "error" in result
        # Must not contain raw traceback or secret-bearing strings
        error_str = str(result)
        assert "Traceback" not in error_str

    def test_broker_error_returns_json_safe_error(self) -> None:
        """Bridge invoke_tool wraps broker unavailability RuntimeError."""
        from fleet_rlm.integrations.daytona.bridge_callbacks import invoke_tool

        interpreter = _StubInterpreter(max_llm_calls=50)
        pred = MagicMock()
        pred.answer = "partial"
        pred.trajectory = [{"output": "[Error] Broker server failed to start within timeout"}]

        with patch("fleet_rlm.runtime.execution.llm_query.build_recursive_subquery_rlm") as mock_build:
            mock_build.return_value = MagicMock(return_value=pred)
            result = invoke_tool(interpreter, "sub_rlm", ["broker task"], {})

        assert isinstance(result, dict)
        # Either a broker RuntimeError was caught or returned as structured
        assert result.get("status") == "error"
        assert "error" in result

    def test_json_safe_value_helper_handles_nested_types(self) -> None:
        """_json_safe_value converts arbitrary types to JSON-safe primitives."""
        from fleet_rlm.integrations.daytona.bridge_callbacks import _json_safe_value

        # Nested dict with non-serializable object
        obj = {"key": object(), "nested": {"a": 1, "b": [1, 2]}}
        result = _json_safe_value(obj)
        assert isinstance(result, dict)
        assert isinstance(result.get("key"), str), "Non-serializable objects become strings"
        assert result["nested"]["a"] == 1
        assert result["nested"]["b"] == [1, 2]

    def test_parent_does_not_crash_on_bridge_failure(self) -> None:
        """invoke_tool exception handling preserves parent execution context."""
        from fleet_rlm.integrations.daytona.bridge_callbacks import invoke_tool

        interpreter = _StubInterpreter(max_llm_calls=50)

        # Simulate a completely unexpected error
        with patch.object(interpreter, "sub_rlm", side_effect=Exception("catastrophic")):
            result = invoke_tool(interpreter, "sub_rlm", ["task"], {})

        # Parent did not crash; got a structured error back
        assert isinstance(result, dict)
        assert result.get("status") == "error"


# ---------------------------------------------------------------------------
# VAL-RLM-020: llm_query_batched preserves order and debits exactly once
# ---------------------------------------------------------------------------


class TestLLMQueryBatchedContracts:
    """VAL-RLM-020: llm_query_batched ordering, budget debit, and timeout recovery."""

    def test_llm_query_batched_returns_results_in_input_order(self) -> None:
        """Results are in the same order as the input prompts."""
        interp = _StubInterpreter(max_llm_calls=10)
        call_order: list[str] = []
        lock = threading.Lock()

        def _ordered_lm(prompt: str) -> str:
            with lock:
                call_order.append(prompt)
            # Simulate slightly staggered completion
            time.sleep(0.01 if "b" in prompt else 0.001)
            return f"result:{prompt}"

        interp.sub_lm = _ordered_lm  # type: ignore[assignment]
        results = interp.llm_query_batched(["a", "b", "c"])

        assert results == ["result:a", "result:b", "result:c"], (
            f"Results must be in input order regardless of completion order: {results}"
        )

    def test_llm_query_batched_debits_budget_once_per_prompt(self) -> None:
        """Budget is debited by len(prompts) exactly once before any LLM call."""
        interp = _StubInterpreter(max_llm_calls=5)
        initial_count = interp._llm_call_count

        def _lm(prompt: str) -> str:
            return f"ok:{prompt}"

        interp.sub_lm = _lm  # type: ignore[assignment]
        interp.llm_query_batched(["a", "b", "c"])

        debited = interp._llm_call_count - initial_count
        assert debited == 3, f"Expected exactly 3 debits for 3 prompts, got {debited}"

    def test_llm_query_batched_rejects_exhausted_budget_before_lm_calls(self) -> None:
        """Budget check happens before any semantic LLM call."""
        interp = _StubInterpreter(max_llm_calls=2)
        interp._llm_call_count = 2  # Exhaust budget

        lm_calls = []

        def _lm(prompt: str) -> str:
            lm_calls.append(prompt)
            return "would not be called"

        interp.sub_lm = _lm  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="LLM call limit exceeded"):
            interp.llm_query_batched(["x", "y"])

        assert lm_calls == [], "No LLM calls should happen on exhausted budget"

    def test_llm_query_batched_returns_empty_for_empty_input(self) -> None:
        """Empty prompt list returns empty result list without budget debit."""
        interp = _StubInterpreter(max_llm_calls=5)
        initial_count = interp._llm_call_count

        result = interp.llm_query_batched([])

        assert result == []
        assert interp._llm_call_count == initial_count, "Empty batch must not debit budget"

    def test_llm_query_batched_reports_per_index_failure_details(self) -> None:
        """Failures include per-prompt index in the error message."""
        interp = _StubInterpreter(max_llm_calls=10)

        def _failing_lm(prompt: str) -> str:
            if "bad" in prompt:
                raise ValueError("bad prompt rejected")
            return f"ok:{prompt}"

        interp.sub_lm = _failing_lm  # type: ignore[assignment]

        with pytest.raises(RuntimeError) as exc_info:
            interp.llm_query_batched(["good", "bad", "also-good"])

        error_msg = str(exc_info.value)
        assert "prompt[" in error_msg, f"Error must include prompt index: {error_msg}"

    def test_llm_query_batched_recovers_after_timeout(self) -> None:
        """After a timeout on one batch, the next batch succeeds normally."""
        interp = _StubInterpreter(max_llm_calls=20, llm_call_timeout=0.05)

        slow_started = threading.Event()

        def _slow_lm(prompt: str) -> str:
            slow_started.set()
            time.sleep(5)
            return "never returned"

        interp.sub_lm = _slow_lm  # type: ignore[assignment]

        # First batch should time out
        with pytest.raises(RuntimeError):
            interp.llm_query_batched(["slow"])

        slow_started.wait(timeout=2)

        # Second batch should succeed with a fast LM
        def _fast_lm(prompt: str) -> str:
            return f"fast:{prompt}"

        interp.sub_lm = _fast_lm  # type: ignore[assignment]
        # Reset budget for the next call
        interp._llm_call_count = 0
        result = interp.llm_query_batched(["quick"])
        assert result == ["fast:quick"], "Second batch after timeout must succeed"

    def test_llm_query_single_debit_on_partial_failure(self) -> None:
        """Budget is fully debited even if some prompts fail — no partial refund."""
        interp = _StubInterpreter(max_llm_calls=5)

        def _sometimes_failing(prompt: str) -> str:
            if prompt == "fail":
                raise RuntimeError("oops")
            return "ok"

        interp.sub_lm = _sometimes_failing  # type: ignore[assignment]

        with pytest.raises(RuntimeError):
            interp.llm_query_batched(["ok", "fail"])

        # Both prompts were debited (1 per prompt)
        assert interp._llm_call_count == 2, (
            f"Expected 2 debits for 2 prompts regardless of failure, got {interp._llm_call_count}"
        )
