"""Tests for the delegate_to_rlm tool.

Covers VAL-RLM-001 through VAL-RLM-003 from the validation contract.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from fleet_rlm.runtime.tools import rlm_delegate as rlm_delegate_mod


class _FakeChildInterpreter:
    def __init__(self, *, started: bool = False, verbose: bool = False) -> None:
        self._started = started
        self.verbose = verbose
        self.sub_lm = None
        self.repo_url: str | None = None
        self.rlm_max_iterations = 20
        self.child_isolation_metadata: dict[str, Any] = {
            "mode": "auto",
            "strategy": "clean",
            "child_sandbox_id": "sbx-child",
        }
        self.start_calls = 0
        self.shutdown_calls = 0
        self.session = _FakeChildSession()

    def start(self) -> None:
        self.start_calls += 1
        self._started = True

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self._started = False

    def _ensure_session_sync(self) -> "_FakeChildSession":
        return self.session


class _FakeChildSession:
    def __init__(self) -> None:
        self.write_calls: list[tuple[str, str]] = []

    def write_file(self, path: str, content: str) -> str:
        self.write_calls.append((path, content))
        return f"/workspace/repo/{path}"


class _FakeParentInterpreter:
    def __init__(self, child: _FakeChildInterpreter, *, remaining: int = 50) -> None:
        self.child = child
        self.remaining = remaining
        self.verbose = child.verbose
        self.build_calls: list[int] = []

    def _remaining_llm_budget(self) -> int:
        return self.remaining

    def build_delegate_child(
        self, *, remaining_llm_budget: int
    ) -> _FakeChildInterpreter:
        self.build_calls.append(remaining_llm_budget)
        return self.child


# ---------------------------------------------------------------------------
# VAL-RLM-001: delegate_to_rlm registered as @tool_fn / in agent's tool registry
# ---------------------------------------------------------------------------


def test_delegate_to_rlm_has_fleet_tool_marker() -> None:
    """VAL-RLM-001: delegate_to_rlm is marked with @tool_fn (__is_fleet_tool__)."""
    assert getattr(rlm_delegate_mod.delegate_to_rlm, "__is_fleet_tool__", False) is True


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
    token = rlm_delegate_mod._delegate_interpreter.set(None)
    try:
        with pytest.raises(RuntimeError, match="bound Daytona interpreter"):
            rlm_delegate_mod.delegate_to_rlm("test query")
    finally:
        rlm_delegate_mod._delegate_interpreter.reset(token)


def test_delegate_to_rlm_starts_sandbox_when_not_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-RLM-002: delegate_to_rlm starts the isolated child sandbox."""
    import dspy

    child = _FakeChildInterpreter(started=False, verbose=False)
    interpreter = _FakeParentInterpreter(child)

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

    assert interpreter.build_calls == [50]
    assert child.start_calls == 1
    assert child.shutdown_calls == 1
    assert result["status"] == "ok"
    assert result["answer"] == "delegated answer"


def test_delegate_to_rlm_reuses_started_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-RLM-002: delegate_to_rlm does not restart an already-started child."""
    import dspy

    child = _FakeChildInterpreter(started=True, verbose=False)
    interpreter = _FakeParentInterpreter(child)

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

    assert child.start_calls == 0
    assert child.shutdown_calls == 1
    assert result["status"] == "ok"
    assert result["answer"] == "reused session result"


def test_delegate_to_rlm_builds_rlm_with_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-RLM-002: delegate_to_rlm constructs dspy.RLM using the bound interpreter."""
    import dspy

    child = _FakeChildInterpreter(started=True, verbose=True)
    interpreter = _FakeParentInterpreter(child, remaining=17)
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
    assert build_calls[0]["interpreter"] is child
    assert build_calls[0]["max_llm_calls"] == 17
    assert build_calls[0]["verbose"] is True


# ---------------------------------------------------------------------------
# VAL-RLM-003: delegate_to_rlm returns structured result
# ---------------------------------------------------------------------------


def test_delegate_to_rlm_returns_ok_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-RLM-003: delegate_to_rlm returns dict with status='ok' and answer."""
    import dspy

    child = _FakeChildInterpreter(started=True, verbose=False)
    interpreter = _FakeParentInterpreter(child)
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
    child = _FakeChildInterpreter(started=True, verbose=False)
    interpreter = _FakeParentInterpreter(child)

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
    assert child.shutdown_calls == 1


def test_delegate_to_rlm_result_is_string_or_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-RLM-003: Result is a string or dict consumable by the agent."""
    import dspy

    child = _FakeChildInterpreter(started=True, verbose=False)
    interpreter = _FakeParentInterpreter(child)
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


def test_delegate_to_rlm_none_document_url_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raw JSON null optional args do not crash delegate context resolution."""
    import dspy

    child = _FakeChildInterpreter(started=True, verbose=False)
    interpreter = _FakeParentInterpreter(child)
    mock_prediction = dspy.Prediction(answer="ok")

    monkeypatch.setattr(
        rlm_delegate_mod,
        "build_recursive_subquery_rlm",
        lambda **kwargs: lambda **kw: mock_prediction,
    )

    token = rlm_delegate_mod._delegate_interpreter.set(interpreter)
    try:
        result = rlm_delegate_mod.delegate_to_rlm(
            "empty answer query",
            document_url=cast(str, None),
        )
    finally:
        rlm_delegate_mod._delegate_interpreter.reset(token)

    assert result["status"] == "ok"
    assert result["answer"] == "ok"


def test_delegate_to_rlm_null_answer_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that never SUBMITs an answer is surfaced as a structured error."""
    import dspy

    child = _FakeChildInterpreter(started=True, verbose=False)
    interpreter = _FakeParentInterpreter(child)
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

    assert result["status"] == "error"
    assert result["reason"] == "null_answer"
    assert "SUBMIT" in result["error"]
    assert child.child_isolation_metadata["error_reason"] == "null_answer"


def test_delegate_to_rlm_empty_string_answer_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit empty string answer remains a successful child result."""
    import dspy

    child = _FakeChildInterpreter(started=True, verbose=False)
    interpreter = _FakeParentInterpreter(child)
    mock_prediction = dspy.Prediction(answer="")

    monkeypatch.setattr(
        rlm_delegate_mod,
        "build_recursive_subquery_rlm",
        lambda **kwargs: lambda **kw: mock_prediction,
    )

    token = rlm_delegate_mod._delegate_interpreter.set(interpreter)
    try:
        result = rlm_delegate_mod.delegate_to_rlm("empty string answer query")
    finally:
        rlm_delegate_mod._delegate_interpreter.reset(token)

    assert result["status"] == "ok"
    assert result["answer"] == ""


def test_delegate_to_rlm_detects_broker_error_in_prediction_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hidden child trajectory broker failures are not returned as status:ok."""
    import dspy

    child = _FakeChildInterpreter(started=True, verbose=False)
    interpreter = _FakeParentInterpreter(child)
    mock_prediction = dspy.Prediction(answer="misleading answer")
    mock_prediction.trajectory = [
        {"output": "[Error] Broker server failed to start within timeout"}
    ]

    monkeypatch.setattr(
        rlm_delegate_mod,
        "build_recursive_subquery_rlm",
        lambda **kwargs: lambda **kw: mock_prediction,
    )

    token = rlm_delegate_mod._delegate_interpreter.set(interpreter)
    try:
        result = rlm_delegate_mod.delegate_to_rlm("broker failure query")
    finally:
        rlm_delegate_mod._delegate_interpreter.reset(token)

    assert result["status"] == "error"
    assert result["reason"] == "broker_unavailable"


def test_delegate_to_rlm_rejects_exhausted_budget() -> None:
    child = _FakeChildInterpreter(started=True, verbose=False)
    interpreter = _FakeParentInterpreter(child, remaining=0)

    token = rlm_delegate_mod._delegate_interpreter.set(interpreter)
    try:
        result = rlm_delegate_mod.delegate_to_rlm("budget exhausted query")
    finally:
        rlm_delegate_mod._delegate_interpreter.reset(token)

    assert result["status"] == "error"
    assert result["reason"] == "budget_exhausted"
    assert interpreter.build_calls == []


def test_delegate_to_rlm_writes_large_document_to_child_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large document_url payloads are written through the child interpreter."""
    import dspy

    import fleet_rlm.runtime.tools.document_tools as document_tools

    child = _FakeChildInterpreter(started=False, verbose=False)
    interpreter = _FakeParentInterpreter(child)
    doc_text = "x" * 100_001
    mock_prediction = dspy.Prediction(answer="document result")

    monkeypatch.setattr(
        document_tools,
        "fetch_document_text",
        lambda url: {"status": "ok", "text": doc_text, "char_count": len(doc_text)},
    )
    monkeypatch.setattr(
        rlm_delegate_mod,
        "build_recursive_subquery_rlm",
        lambda **kwargs: lambda **kw: mock_prediction,
    )

    token = rlm_delegate_mod._delegate_interpreter.set(interpreter)
    try:
        result = rlm_delegate_mod.delegate_to_rlm(
            "read the document",
            document_url="https://example.com/doc.txt",
        )
    finally:
        rlm_delegate_mod._delegate_interpreter.reset(token)

    assert result["status"] == "ok"
    assert child.session.write_calls == [
        ("artifacts/rlm-inputs/doc_41cb62f6e140.txt", doc_text)
    ]
    assert child.start_calls == 1
    assert child.shutdown_calls == 1


def test_delegate_to_rlm_stages_local_workspace_snapshot_for_codebase_tasks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Clean child sandboxes get explicit local repo context for codebase tasks."""
    import dspy

    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    src_dir = tmp_path / "src" / "demo"
    src_dir.mkdir(parents=True)
    (src_dir / "runtime.py").write_text(
        "def build_delegate_child():\n    return 'sandbox budget session'\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    child = _FakeChildInterpreter(started=False, verbose=False)
    interpreter = _FakeParentInterpreter(child)
    seen_contexts: list[str] = []

    def _mock_build(**kwargs: Any) -> Any:
        def _module(**kw: Any) -> dspy.Prediction:
            seen_contexts.append(str(kw.get("context", "")))
            return dspy.Prediction(answer="snapshot-backed answer")

        return _module

    monkeypatch.setattr(rlm_delegate_mod, "build_recursive_subquery_rlm", _mock_build)

    token = rlm_delegate_mod._delegate_interpreter.set(interpreter)
    try:
        result = rlm_delegate_mod.delegate_to_rlm(
            "Inspect the codebase implementation for sandbox budget session restore",
        )
    finally:
        rlm_delegate_mod._delegate_interpreter.reset(token)

    assert result["status"] == "ok"
    assert child.session.write_calls
    snapshot_path, snapshot = child.session.write_calls[0]
    assert snapshot_path == "artifacts/rlm-inputs/local_workspace_snapshot.txt"
    assert "--- FILE: src/demo/runtime.py ---" in snapshot
    assert "sandbox budget session" in snapshot
    assert "local_workspace_snapshot.txt" in seen_contexts[0]
    assert child.child_isolation_metadata["local_workspace_snapshot_path"].endswith(
        "local_workspace_snapshot.txt"
    )


def test_delegate_to_rlm_skips_local_workspace_snapshot_for_repo_children(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Repo-backed child sandboxes already have target repo context."""
    import dspy

    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    src_dir = tmp_path / "src" / "demo"
    src_dir.mkdir(parents=True)
    (src_dir / "runtime.py").write_text(
        "def build_delegate_child():\n    return 'sandbox budget session'\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    child = _FakeChildInterpreter(started=True, verbose=False)
    child.repo_url = "https://github.com/example/repo.git"
    interpreter = _FakeParentInterpreter(child)
    seen_contexts: list[str] = []

    def _mock_build(**kwargs: Any) -> Any:
        def _module(**kw: Any) -> dspy.Prediction:
            seen_contexts.append(str(kw.get("context", "")))
            return dspy.Prediction(answer="repo-backed answer")

        return _module

    monkeypatch.setattr(rlm_delegate_mod, "build_recursive_subquery_rlm", _mock_build)

    token = rlm_delegate_mod._delegate_interpreter.set(interpreter)
    try:
        result = rlm_delegate_mod.delegate_to_rlm(
            "Inspect the codebase implementation for sandbox budget session restore",
        )
    finally:
        rlm_delegate_mod._delegate_interpreter.reset(token)

    assert result["status"] == "ok"
    assert child.session.write_calls == []
    assert "local_workspace_snapshot.txt" not in seen_contexts[0]


# ---------------------------------------------------------------------------
# set_delegate_interpreter utility
# ---------------------------------------------------------------------------


def test_set_delegate_interpreter_returns_token() -> None:
    """set_delegate_interpreter returns a Token that can reset the variable."""
    from contextvars import Token

    token = rlm_delegate_mod.set_delegate_interpreter(None)
    assert isinstance(token, Token)
    rlm_delegate_mod._delegate_interpreter.reset(token)


def test_set_delegate_interpreter_sets_value() -> None:
    """set_delegate_interpreter makes the interpreter visible to delegate_to_rlm."""
    sentinel = object()
    token = rlm_delegate_mod.set_delegate_interpreter(sentinel)
    try:
        assert rlm_delegate_mod._delegate_interpreter.get() is sentinel
    finally:
        rlm_delegate_mod._delegate_interpreter.reset(token)
