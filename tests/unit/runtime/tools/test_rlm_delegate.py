"""Tests for the delegate_to_rlm tool.

Covers VAL-RLM-001 through VAL-RLM-003 from the validation contract.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# VAL-RLM-001: delegate_to_rlm registered as @tool_fn / in agent's tool registry
# ---------------------------------------------------------------------------


def test_delegate_to_rlm_has_fleet_tool_marker() -> None:
    """VAL-RLM-001: delegate_to_rlm is marked with @tool_fn (__is_fleet_tool__)."""
    from fleet_rlm.runtime.tools.rlm_delegate import delegate_to_rlm

    assert getattr(delegate_to_rlm, "__is_fleet_tool__", False) is True


def test_delegate_to_rlm_in_discover_tools() -> None:
    """VAL-RLM-001: discover_tools() includes delegate_to_rlm in the registry."""
    from fleet_rlm.runtime.tools import discover_tools

    tools = discover_tools()
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}

    assert "delegate_to_rlm" in names, (
        f"delegate_to_rlm not found in registry. Found: {sorted(names)}"
    )


def test_delegate_to_rlm_valid_for_react() -> None:
    """VAL-RLM-001: dspy.ReAct can be constructed with delegate_to_rlm in tools."""
    import dspy

    from fleet_rlm.runtime.agent.agent import FleetAgentSignature
    from fleet_rlm.runtime.tools import discover_tools

    tools = discover_tools()
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}
    assert "delegate_to_rlm" in names

    # dspy.ReAct construction with the full tool list must not raise
    react = dspy.ReAct(FleetAgentSignature, tools=tools, max_iters=1)
    assert react is not None


# ---------------------------------------------------------------------------
# VAL-RLM-002: delegate_to_rlm executes in Daytona sandbox
# ---------------------------------------------------------------------------


def test_delegate_to_rlm_raises_without_interpreter() -> None:
    """delegate_to_rlm raises RuntimeError when no interpreter is in context."""
    from fleet_rlm.runtime.tools.rlm_delegate import (
        _delegate_interpreter,
        delegate_to_rlm,
    )

    token = _delegate_interpreter.set(None)
    try:
        with pytest.raises(RuntimeError, match="bound Daytona interpreter"):
            delegate_to_rlm("test query")
    finally:
        _delegate_interpreter.reset(token)


def test_delegate_to_rlm_starts_sandbox_when_not_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-RLM-002: delegate_to_rlm calls interpreter.start() to create the sandbox."""
    import dspy

    import fleet_rlm.runtime.tools.rlm_delegate as rlm_delegate_mod

    started_calls: list[None] = []

    interpreter = SimpleNamespace(
        _started=False,
        verbose=False,
    )

    def _mock_start() -> None:
        started_calls.append(None)
        interpreter._started = True  # type: ignore[union-attr]

    interpreter.start = _mock_start  # type: ignore[union-attr]

    mock_prediction = dspy.Prediction(answer="delegated answer")

    monkeypatch.setattr(
        rlm_delegate_mod,
        "build_recursive_subquery_rlm",
        lambda **kwargs: lambda **kw: mock_prediction,
    )

    token = rlm_delegate_mod._delegate_interpreter.set(interpreter)
    try:
        result = rlm_delegate_mod.delegate_to_rlm("test query", "test context")
    finally:
        rlm_delegate_mod._delegate_interpreter.reset(token)

    assert len(started_calls) == 1, "interpreter.start() should have been called once"
    assert result["status"] == "ok"
    assert result["answer"] == "delegated answer"


def test_delegate_to_rlm_reuses_started_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-RLM-002: delegate_to_rlm reuses an already-started sandbox session."""
    import dspy

    import fleet_rlm.runtime.tools.rlm_delegate as rlm_delegate_mod

    started_calls: list[None] = []

    interpreter = SimpleNamespace(
        _started=True,
        verbose=False,
    )

    def _unexpected_start() -> None:  # pragma: no cover
        started_calls.append(None)

    interpreter.start = _unexpected_start  # type: ignore[union-attr]

    mock_prediction = dspy.Prediction(answer="reused session result")

    monkeypatch.setattr(
        rlm_delegate_mod,
        "build_recursive_subquery_rlm",
        lambda **kwargs: lambda **kw: mock_prediction,
    )

    token = rlm_delegate_mod._delegate_interpreter.set(interpreter)
    try:
        result = rlm_delegate_mod.delegate_to_rlm("query about reuse")
    finally:
        rlm_delegate_mod._delegate_interpreter.reset(token)

    assert len(started_calls) == 0, "interpreter.start() should NOT have been called"
    assert result["status"] == "ok"
    assert result["answer"] == "reused session result"


def test_delegate_to_rlm_builds_rlm_with_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-RLM-002: delegate_to_rlm constructs dspy.RLM using the bound interpreter."""
    import dspy

    import fleet_rlm.runtime.tools.rlm_delegate as rlm_delegate_mod

    interpreter = SimpleNamespace(_started=True, verbose=True)
    build_calls: list[dict[str, Any]] = []

    mock_prediction = dspy.Prediction(answer="rlm result")

    def _mock_build(**kwargs: Any) -> Any:
        build_calls.append(dict(kwargs))
        return lambda **kw: mock_prediction

    monkeypatch.setattr(rlm_delegate_mod, "build_recursive_subquery_rlm", _mock_build)

    token = rlm_delegate_mod._delegate_interpreter.set(interpreter)
    try:
        rlm_delegate_mod.delegate_to_rlm("build test query")
    finally:
        rlm_delegate_mod._delegate_interpreter.reset(token)

    assert len(build_calls) == 1
    assert build_calls[0]["interpreter"] is interpreter
    assert build_calls[0]["verbose"] is True


# ---------------------------------------------------------------------------
# VAL-RLM-003: delegate_to_rlm returns structured result
# ---------------------------------------------------------------------------


def test_delegate_to_rlm_returns_ok_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-RLM-003: delegate_to_rlm returns dict with status='ok' and answer."""
    import dspy

    import fleet_rlm.runtime.tools.rlm_delegate as rlm_delegate_mod

    interpreter = SimpleNamespace(_started=True, verbose=False)
    mock_prediction = dspy.Prediction(answer="structured answer from RLM")

    monkeypatch.setattr(
        rlm_delegate_mod,
        "build_recursive_subquery_rlm",
        lambda **kwargs: lambda **kw: mock_prediction,
    )

    token = rlm_delegate_mod._delegate_interpreter.set(interpreter)
    try:
        result = rlm_delegate_mod.delegate_to_rlm("structured query")
    finally:
        rlm_delegate_mod._delegate_interpreter.reset(token)

    assert isinstance(result, dict)
    assert result["status"] == "ok"
    assert result["answer"] == "structured answer from RLM"


def test_delegate_to_rlm_returns_error_dict_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-RLM-003: delegate_to_rlm returns dict with status='error' on exception."""
    import fleet_rlm.runtime.tools.rlm_delegate as rlm_delegate_mod

    interpreter = SimpleNamespace(_started=True, verbose=False)

    def _failing_rlm(**kwargs: Any) -> Any:
        raise RuntimeError("RLM execution failed")

    monkeypatch.setattr(
        rlm_delegate_mod,
        "build_recursive_subquery_rlm",
        lambda **kwargs: _failing_rlm,
    )

    token = rlm_delegate_mod._delegate_interpreter.set(interpreter)
    try:
        result = rlm_delegate_mod.delegate_to_rlm("failing query")
    finally:
        rlm_delegate_mod._delegate_interpreter.reset(token)

    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert "error" in result
    assert "RLM execution failed" in result["error"]


def test_delegate_to_rlm_result_is_string_or_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-RLM-003: Result is a string or dict consumable by the agent."""
    import dspy

    import fleet_rlm.runtime.tools.rlm_delegate as rlm_delegate_mod

    interpreter = SimpleNamespace(_started=True, verbose=False)
    mock_prediction = dspy.Prediction(answer="agent-consumable result")

    monkeypatch.setattr(
        rlm_delegate_mod,
        "build_recursive_subquery_rlm",
        lambda **kwargs: lambda **kw: mock_prediction,
    )

    token = rlm_delegate_mod._delegate_interpreter.set(interpreter)
    try:
        result = rlm_delegate_mod.delegate_to_rlm("agent query")
    finally:
        rlm_delegate_mod._delegate_interpreter.reset(token)

    assert isinstance(result, (str, dict)), (
        f"Result must be str or dict, got {type(result)}"
    )


def test_delegate_to_rlm_empty_answer_returns_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-RLM-003: Empty answer field produces empty string, not None."""
    import dspy

    import fleet_rlm.runtime.tools.rlm_delegate as rlm_delegate_mod

    interpreter = SimpleNamespace(_started=True, verbose=False)
    mock_prediction = dspy.Prediction(answer=None)

    monkeypatch.setattr(
        rlm_delegate_mod,
        "build_recursive_subquery_rlm",
        lambda **kwargs: lambda **kw: mock_prediction,
    )

    token = rlm_delegate_mod._delegate_interpreter.set(interpreter)
    try:
        result = rlm_delegate_mod.delegate_to_rlm("empty answer query")
    finally:
        rlm_delegate_mod._delegate_interpreter.reset(token)

    assert result["status"] == "ok"
    assert result["answer"] == ""


# ---------------------------------------------------------------------------
# set_delegate_interpreter utility
# ---------------------------------------------------------------------------


def test_set_delegate_interpreter_returns_token() -> None:
    """set_delegate_interpreter returns a Token that can reset the variable."""
    from contextvars import Token

    from fleet_rlm.runtime.tools.rlm_delegate import (
        _delegate_interpreter,
        set_delegate_interpreter,
    )

    token = set_delegate_interpreter(None)
    assert isinstance(token, Token)
    _delegate_interpreter.reset(token)


def test_set_delegate_interpreter_sets_value() -> None:
    """set_delegate_interpreter makes the interpreter visible to delegate_to_rlm."""
    from fleet_rlm.runtime.tools.rlm_delegate import (
        _delegate_interpreter,
        set_delegate_interpreter,
    )

    sentinel = object()
    token = set_delegate_interpreter(sentinel)
    try:
        assert _delegate_interpreter.get() is sentinel
    finally:
        _delegate_interpreter.reset(token)
