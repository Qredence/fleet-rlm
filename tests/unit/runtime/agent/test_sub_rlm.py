"""Tests for sub_rlm() symbolic recursion primitive (Algorithm 1, arXiv 2512.24601v2)."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from fleet_rlm.runtime.execution.interpreter_support import (
    initialize_llm_query_state,
    initialize_sub_rlm_state,
)
from fleet_rlm.runtime.execution.llm_query import LLMQueryMixin


class _StubInterpreter(LLMQueryMixin):
    """Minimal interpreter stub with the LLMQueryMixin for testing sub_rlm."""

    def __init__(
        self,
        *,
        max_llm_calls: int = 50,
        llm_call_timeout: int = 60,
        depth: int = 0,
        max_depth: int = 2,
    ) -> None:
        initialize_llm_query_state(
            self,
            sub_lm=None,
            max_llm_calls=max_llm_calls,
            llm_call_timeout=llm_call_timeout,
        )
        initialize_sub_rlm_state(self, depth=depth, max_depth=max_depth)
        self.child_budgets: list[int] = []

    def build_delegate_child(self, *, remaining_llm_budget: int) -> Any:
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


def _make_fake_prediction(answer: Any = "test answer") -> Any:
    pred = MagicMock()
    pred.answer = answer
    return pred


# --- Depth enforcement ---


def test_sub_rlm_rejects_at_max_depth() -> None:
    interp = _StubInterpreter(depth=2, max_depth=2)
    with pytest.raises(RuntimeError, match="max recursion depth"):
        interp.sub_rlm("hello")


def test_sub_rlm_batched_rejects_at_max_depth() -> None:
    interp = _StubInterpreter(depth=2, max_depth=2)
    with pytest.raises(RuntimeError, match="max recursion depth"):
        interp.sub_rlm_batched(["a", "b"])


# --- Budget enforcement ---


def test_sub_rlm_rejects_exhausted_budget() -> None:
    interp = _StubInterpreter(max_llm_calls=5, depth=0)
    interp._llm_call_count = 5
    with pytest.raises(RuntimeError, match="budget exhausted"):
        interp.sub_rlm("hello")


def test_sub_rlm_batched_siblings_share_llm_budget() -> None:
    interp = _StubInterpreter(max_llm_calls=2, depth=0, max_depth=2)

    with pytest.raises(RuntimeError, match="cannot spawn 3 sub_rlm children"):
        interp.sub_rlm_batched(["a", "b", "c"])

    assert interp._llm_call_count == 0
    assert interp.child_budgets == []


def test_sub_rlm_batched_siblings_receive_bounded_budget_leases() -> None:
    interp = _StubInterpreter(max_llm_calls=5, depth=0, max_depth=2)

    def _fake_build(**kwargs: Any) -> MagicMock:
        child = kwargs["interpreter"]

        def _module(*, prompt: str, context: str) -> Any:
            _ = context
            child._check_and_increment_llm_calls(1)
            child._check_and_increment_llm_calls(1)
            return _make_fake_prediction(f"answer {prompt}")

        return MagicMock(side_effect=_module)

    with (
        patch(
            "fleet_rlm.runtime.execution.llm_query.build_recursive_subquery_rlm",
            side_effect=_fake_build,
        ),
        pytest.raises(RuntimeError, match="sub_rlm_batched failed"),
    ):
        interp.sub_rlm_batched(["a", "b", "c"])

    assert interp._llm_call_count <= 5
    assert sorted(interp.child_budgets) == [1, 2, 2]


# --- Successful execution ---


def test_sub_rlm_calls_child_module_and_returns_answer() -> None:
    interp = _StubInterpreter(max_llm_calls=50, depth=0, max_depth=2)

    fake_pred = _make_fake_prediction("child answer")

    with patch(
        "fleet_rlm.runtime.execution.llm_query.build_recursive_subquery_rlm"
    ) as mock_build:
        mock_module = MagicMock(return_value=fake_pred)
        mock_build.return_value = mock_module

        result = interp.sub_rlm("summarize this chunk")

    assert result == "child answer"
    mock_module.assert_called_once_with(prompt="summarize this chunk", context="")


def test_sub_rlm_passes_context() -> None:
    interp = _StubInterpreter(max_llm_calls=50, depth=0, max_depth=2)
    fake_pred = _make_fake_prediction("ok")

    with patch(
        "fleet_rlm.runtime.execution.llm_query.build_recursive_subquery_rlm"
    ) as mock_build:
        mock_module = MagicMock(return_value=fake_pred)
        mock_build.return_value = mock_module

        interp.sub_rlm("classify", context="extra info")

    mock_module.assert_called_once_with(prompt="classify", context="extra info")


def test_sub_rlm_null_answer_raises_runtime_error() -> None:
    interp = _StubInterpreter(max_llm_calls=50, depth=0, max_depth=2)

    with (
        patch(
            "fleet_rlm.runtime.execution.llm_query.build_recursive_subquery_rlm",
            return_value=MagicMock(return_value=_make_fake_prediction(None)),
        ),
        pytest.raises(RuntimeError, match="without SUBMIT"),
    ):
        interp.sub_rlm("no submit")


def test_sub_rlm_detects_broker_error_in_child_prediction() -> None:
    interp = _StubInterpreter(max_llm_calls=50, depth=0, max_depth=2)
    prediction = _make_fake_prediction("misleading answer")
    prediction.trajectory = [
        {"output": "[Error] Broker server failed to start within timeout"}
    ]

    with (
        patch(
            "fleet_rlm.runtime.execution.llm_query.build_recursive_subquery_rlm",
            return_value=MagicMock(return_value=prediction),
        ),
        pytest.raises(RuntimeError, match="broker unavailable"),
    ):
        interp.sub_rlm("broker issue")


def test_sub_rlm_batched_returns_ordered_results() -> None:
    interp = _StubInterpreter(max_llm_calls=50, depth=0, max_depth=2)
    call_count = 0

    def _fake_build(**kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        idx = call_count
        pred = _make_fake_prediction(f"answer-{idx}")
        return MagicMock(return_value=pred)

    with patch(
        "fleet_rlm.runtime.execution.llm_query.build_recursive_subquery_rlm",
        side_effect=_fake_build,
    ):
        results = interp.sub_rlm_batched(["a", "b", "c"])

    assert len(results) == 3
    assert all(r.startswith("answer-") for r in results)


def test_sub_rlm_empty_prompt_raises() -> None:
    interp = _StubInterpreter()
    with pytest.raises(ValueError, match="empty"):
        interp.sub_rlm("")


def test_sub_rlm_batched_empty_list_returns_empty() -> None:
    interp = _StubInterpreter()
    assert interp.sub_rlm_batched([]) == []


# --- Depth propagation ---


def test_child_depth_incremented() -> None:
    interp = _StubInterpreter(depth=0, max_depth=3)

    children_captured: list[Any] = []
    original_build = interp.build_delegate_child

    def _capture_child(**kwargs: Any) -> Any:
        child = original_build(**kwargs)
        children_captured.append(child)
        return child

    interp.build_delegate_child = _capture_child

    with patch(
        "fleet_rlm.runtime.execution.llm_query.build_recursive_subquery_rlm"
    ) as mock_build:
        mock_build.return_value = MagicMock(return_value=_make_fake_prediction("ok"))
        interp.sub_rlm("test")

    assert len(children_captured) == 1
    child = children_captured[0]
    assert child._sub_rlm_depth == 1
    assert child._sub_rlm_max_depth == 3


# --- Bridge tools integration ---


def test_bridge_tools_includes_sub_rlm() -> None:
    from fleet_rlm.integrations.daytona.interpreter import bridge_tools

    interp = _StubInterpreter()
    interp._tools = {}  # type: ignore[attr-defined]
    tools = bridge_tools(interp)
    assert "sub_rlm" in tools
    assert "sub_rlm_batched" in tools
    assert "llm_query" in tools
    assert "llm_query_batched" in tools


# --- Unsupported callbacks enforcement ---


def test_rlm_query_still_blocked_in_sandbox_code() -> None:
    from fleet_rlm.integrations.daytona.interpreter import (
        reject_unsupported_recursive_callbacks,
    )

    interp = _StubInterpreter()
    interp._tools = {}  # type: ignore[attr-defined]

    with pytest.raises(Exception, match="not available"):
        reject_unsupported_recursive_callbacks(interp, 'result = rlm_query("hi")')

    # sub_rlm should NOT be blocked
    reject_unsupported_recursive_callbacks(interp, 'result = sub_rlm("hi")')


# --- Child sandbox isolation policy ---


def test_build_delegate_child_context_mode_reuses_parent_sandbox() -> None:
    """Context mode preserves legacy same-sandbox fresh-context behavior."""
    from fleet_rlm.integrations.daytona.interpreter import build_delegate_child
    from fleet_rlm.integrations.daytona.runtime import (
        DaytonaSandboxSession,
    )

    # Use a real DaytonaSandboxSession for the child._session assertion
    parent_sandbox = MagicMock()
    parent_session = DaytonaSandboxSession(
        sandbox=parent_sandbox,
        repo_url=None,
        ref=None,
        volume_name="vol",
        workspace_path="/workspace",
        context_sources=[],
        volume_mount_path="/mnt",
        context_id="ctx-parent",
    )

    parent = MagicMock()
    parent.runtime = MagicMock()
    parent.runtime._resolved_config = SimpleNamespace(
        api_key="test-key",
        api_url="https://daytona.invalid",
        target=None,
    )
    parent.timeout = 60
    parent.execute_timeout = 60
    parent.volume_name = "vol"
    parent.repo_url = None
    parent.repo_ref = None
    parent.context_paths = []
    parent.sandbox_spec = None
    parent.sub_lm = None
    parent.llm_call_timeout = 30
    parent.async_execute = True
    parent.rlm_max_iterations = 30
    parent.child_isolation_mode = "context"
    parent.child_fork_fallback = "clean"
    parent.delegate_max_calls_per_turn = 8
    parent.delegate_result_truncation_chars = 8000
    parent._sub_rlm_depth = 0
    parent._sub_rlm_max_depth = 2
    parent._session = parent_session
    parent._parent_session_for_child.return_value = parent_session

    child = build_delegate_child(parent, remaining_llm_budget=10)

    # The child's _session is a new DaytonaSandboxSession on same sandbox
    assert isinstance(child._session, DaytonaSandboxSession)
    assert child._session.sandbox is parent_sandbox
    assert child.delete_context_on_shutdown is True
    # Fresh context (None forces create_context() on start)
    assert child._session.context_id is None


def test_build_delegate_child_auto_fallback_no_session() -> None:
    """When parent has no session, auto mode creates a clean child sandbox."""
    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

    runtime = MagicMock()
    runtime._resolved_config = SimpleNamespace(
        api_key="test-key",
        api_url="https://daytona.invalid",
        target=None,
    )
    parent = DaytonaInterpreter(
        runtime=runtime,
        child_isolation_mode="auto",
        child_fork_fallback="clean",
    )

    child = parent.build_delegate_child(remaining_llm_budget=10)

    assert child._session is None
    assert child.delete_session_on_shutdown is True
    assert child.child_isolation_metadata["mode"] == "auto"
    assert child.child_isolation_metadata["strategy"] == "clean"
    assert child.child_isolation_metadata["reason"] == "no_parent_session"


# --- ThreadPoolExecutor timeout handling ---


def test_llm_query_executor_survives_timeout() -> None:
    """VAL-BACKEND-RUNTIME-001: After timeout, the executor remains usable.

    With a single-worker pool, a long-running LM call blocks the executor.
    Without the fix, the next quick call would be stuck in the queue behind
    the zombie thread and also time out.  With the fix, a fresh executor is
    created after each timeout so subsequent calls succeed promptly.
    """
    interp = _StubInterpreter(max_llm_calls=1, llm_call_timeout=0.1)

    slow_started = threading.Event()

    def _slow_lm(prompt: str) -> str:
        slow_started.set()
        time.sleep(5)  # Far longer than the 0.1s timeout
        return "slow"

    interp.sub_lm = _slow_lm  # type: ignore[assignment]

    # First call starts the slow LM and times out
    with pytest.raises(RuntimeError, match="timed out"):
        interp._query_sub_lm("prompt")

    # Wait until we are sure the slow thread has started
    slow_started.wait(timeout=2)

    # Second call should succeed on a fresh executor
    def _fast_lm(prompt: str) -> str:
        return "fast"

    interp.sub_lm = _fast_lm  # type: ignore[assignment]
    result = interp._query_sub_lm("prompt")
    assert result == "fast"
