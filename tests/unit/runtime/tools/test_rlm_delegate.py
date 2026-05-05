"""Tests for the delegate_to_rlm tool.

Covers VAL-RLM-001 through VAL-RLM-003 from the validation contract.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from fleet_rlm.runtime.tools import rlm_delegate as rlm_delegate_mod


class _FakeChildInterpreter:
    def __init__(self, *, started: bool = False, verbose: bool = False) -> None:
        self._started = started
        self.verbose = verbose
        self.sub_lm = None
        self.repo_url: str | None = None
        self.volume_name: str | None = None
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
        self.repo_url: str | None = None
        self.volume_name: str | None = None
        self.write_calls: list[tuple[str, str]] = []

    def write_file(self, path: str, content: str) -> str:
        self.write_calls.append((path, content))
        return f"/workspace/repo/{path}"


class _FakeParentInterpreter:
    def __init__(
        self,
        child: _FakeChildInterpreter | list[_FakeChildInterpreter],
        *,
        remaining: int = 50,
    ) -> None:
        self.children = child if isinstance(child, list) else [child]
        self.child = self.children[0]
        self.remaining = remaining
        self.verbose = self.child.verbose
        self.build_calls: list[int] = []
        self.lease_calls: list[int] = []

    def _remaining_llm_budget(self) -> int:
        return self.remaining

    def build_delegate_child(
        self, *, remaining_llm_budget: int
    ) -> _FakeChildInterpreter:
        self.build_calls.append(remaining_llm_budget)
        index = min(len(self.build_calls) - 1, len(self.children) - 1)
        return self.children[index]

    def _install_child_budget_lease(
        self,
        child: _FakeChildInterpreter,
        lease: int,
    ) -> None:
        self.lease_calls.append(lease)
        child.max_llm_calls = lease
        child.child_isolation_metadata["llm_budget_lease"] = lease


# ---------------------------------------------------------------------------
# VAL-RLM-001: delegate_to_rlm registered as @tool_fn / in agent's tool registry
# ---------------------------------------------------------------------------


def test_delegate_to_rlm_has_fleet_tool_marker() -> None:
    """VAL-RLM-001: delegate_to_rlm is marked with @tool_fn (__is_fleet_tool__)."""
    assert getattr(rlm_delegate_mod.delegate_to_rlm, "__is_fleet_tool__", False) is True


def test_delegate_to_rlm_batched_has_fleet_tool_marker() -> None:
    """delegate_to_rlm_batched is marked with @tool_fn for registry discovery."""
    assert (
        getattr(rlm_delegate_mod.delegate_to_rlm_batched, "__is_fleet_tool__", False)
        is True
    )


def test_delegate_to_rlm_in_discover_tools() -> None:
    """VAL-RLM-001: discover_tools() includes RLM delegation tools."""
    from fleet_rlm.runtime.tools import discover_tools

    tools = discover_tools()
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}

    assert "delegate_to_rlm" in names, (
        f"delegate_to_rlm not found in registry. Found: {sorted(names)}"
    )
    assert "delegate_to_rlm_batched" in names, (
        f"delegate_to_rlm_batched not found in registry. Found: {sorted(names)}"
    )


def test_delegate_to_rlm_valid_for_react() -> None:
    """VAL-RLM-001: dspy.ReAct can be constructed with delegate_to_rlm in tools."""
    import dspy

    from fleet_rlm.runtime.agent.agent import FleetAgentSignature
    from fleet_rlm.runtime.tools import discover_tools

    tools = discover_tools()
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}
    assert "delegate_to_rlm" in names
    assert "delegate_to_rlm_batched" in names

    # dspy.ReAct construction with the full tool list must not raise
    react = dspy.ReAct(FleetAgentSignature, tools=tools, max_iters=1)
    assert react is not None


# ---------------------------------------------------------------------------
# VAL-RLM-002: delegate_to_rlm executes in Daytona sandbox
# ---------------------------------------------------------------------------


def test_delegate_to_rlm_raises_without_interpreter() -> None:
    """delegate_to_rlm raises RuntimeError when no interpreter is passed."""
    with pytest.raises(RuntimeError, match="Daytona interpreter"):
        rlm_delegate_mod.delegate_to_rlm("test query")


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

    result = rlm_delegate_mod.delegate_to_rlm(
        "test query", "test context", interpreter=interpreter
    )

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

    result = rlm_delegate_mod.delegate_to_rlm(
        "query about reuse", interpreter=interpreter
    )

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

    rlm_delegate_mod.delegate_to_rlm("build test query", interpreter=interpreter)

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

    result = rlm_delegate_mod.delegate_to_rlm(
        "structured query", interpreter=interpreter
    )

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

    result = rlm_delegate_mod.delegate_to_rlm("failing query", interpreter=interpreter)

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

    result = rlm_delegate_mod.delegate_to_rlm("agent query", interpreter=interpreter)

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

    result = rlm_delegate_mod.delegate_to_rlm(
        "empty answer query", document_url=None, interpreter=interpreter
    )

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

    result = rlm_delegate_mod.delegate_to_rlm(
        "empty answer query", interpreter=interpreter
    )

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

    result = rlm_delegate_mod.delegate_to_rlm(
        "empty string answer query", interpreter=interpreter
    )

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

    result = rlm_delegate_mod.delegate_to_rlm(
        "broker failure query", interpreter=interpreter
    )

    assert result["status"] == "error"
    assert result["reason"] == "broker_unavailable"


def test_delegate_to_rlm_rejects_exhausted_budget() -> None:
    child = _FakeChildInterpreter(started=True, verbose=False)
    interpreter = _FakeParentInterpreter(child, remaining=0)

    result = rlm_delegate_mod.delegate_to_rlm(
        "budget exhausted query", interpreter=interpreter
    )

    assert result["status"] == "error"
    assert result["reason"] == "budget_exhausted"
    assert interpreter.build_calls == []


def test_delegate_to_rlm_batched_preserves_order_and_leases_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batched delegation returns ordered answers and bounded sibling budgets."""
    import dspy

    children = [_FakeChildInterpreter(started=True) for _ in range(3)]
    interpreter = _FakeParentInterpreter(children, remaining=5)

    def _mock_build(**kwargs: Any) -> Any:
        def _module(**kw: Any) -> dspy.Prediction:
            return dspy.Prediction(answer=f"answer:{kw['prompt']}:{kw['context']}")

        return _module

    monkeypatch.setattr(rlm_delegate_mod, "build_recursive_subquery_rlm", _mock_build)

    result = rlm_delegate_mod.delegate_to_rlm_batched(
        ["alpha", "beta", "gamma"], context="shared", interpreter=interpreter
    )

    assert result == {
        "status": "ok",
        "results": [
            {"query": "alpha", "answer": "answer:alpha:shared"},
            {"query": "beta", "answer": "answer:beta:shared"},
            {"query": "gamma", "answer": "answer:gamma:shared"},
        ],
    }
    assert sorted(interpreter.build_calls) == [1, 2, 2]
    assert sorted(interpreter.lease_calls) == [1, 2, 2]
    assert [child.shutdown_calls for child in children] == [1, 1, 1]


def test_delegate_to_rlm_batched_reports_partial_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed siblings are reported without dropping successful child answers."""
    import dspy

    children = [_FakeChildInterpreter(started=True) for _ in range(3)]
    interpreter = _FakeParentInterpreter(children, remaining=6)

    def _mock_build(**kwargs: Any) -> Any:
        def _module(**kw: Any) -> dspy.Prediction:
            if kw["prompt"] == "bad":
                raise RuntimeError("child failed")
            return dspy.Prediction(answer=f"ok:{kw['prompt']}")

        return _module

    monkeypatch.setattr(rlm_delegate_mod, "build_recursive_subquery_rlm", _mock_build)

    result = rlm_delegate_mod.delegate_to_rlm_batched(
        ["good", "bad", "later"], interpreter=interpreter
    )

    assert result["status"] == "error"
    assert result["results"] == [
        {"query": "good", "answer": "ok:good"},
        {"query": "later", "answer": "ok:later"},
    ]
    assert result["errors"] == [
        {
            "index": 1,
            "query": "bad",
            "reason": "child_error",
            "error": "child failed",
        }
    ]
    assert [child.shutdown_calls for child in children] == [1, 1, 1]


def test_delegate_to_rlm_batched_overlaps_child_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sibling child RLMs run concurrently rather than serially."""
    import dspy

    children = [_FakeChildInterpreter(started=True) for _ in range(3)]
    interpreter = _FakeParentInterpreter(children, remaining=6)
    barrier = threading.Barrier(3)

    def _mock_build(**kwargs: Any) -> Any:
        def _module(**kw: Any) -> dspy.Prediction:
            barrier.wait(timeout=2)
            return dspy.Prediction(answer=f"done:{kw['prompt']}")

        return _module

    monkeypatch.setattr(rlm_delegate_mod, "build_recursive_subquery_rlm", _mock_build)

    result = rlm_delegate_mod.delegate_to_rlm_batched(
        ["a", "b", "c"], interpreter=interpreter
    )

    assert result["status"] == "ok"
    assert [item["answer"] for item in result["results"]] == [
        "done:a",
        "done:b",
        "done:c",
    ]


def test_delegate_to_rlm_batched_rejects_exhausted_budget() -> None:
    children = [_FakeChildInterpreter(started=True) for _ in range(3)]
    interpreter = _FakeParentInterpreter(children, remaining=2)

    result = rlm_delegate_mod.delegate_to_rlm_batched(
        ["a", "b", "c"], interpreter=interpreter
    )

    assert result["status"] == "error"
    assert result["reason"] == "budget_exhausted"
    assert interpreter.build_calls == []
    assert [error["reason"] for error in result["errors"]] == [
        "budget_exhausted",
        "budget_exhausted",
        "budget_exhausted",
    ]


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

    result = rlm_delegate_mod.delegate_to_rlm(
        "read the document",
        document_url="https://example.com/doc.txt",
        interpreter=interpreter,
    )

    assert result["status"] == "ok"
    assert child.session.write_calls == [
        ("artifacts/rlm-inputs/doc_41cb62f6e140.txt", doc_text)
    ]
    assert child.start_calls == 1
    assert child.shutdown_calls == 1


def test_delegate_to_rlm_embeds_truncated_large_document_when_child_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large document_url payloads degrade to truncated context if staging fails."""
    import dspy

    import fleet_rlm.runtime.tools.document_tools as document_tools

    child = _FakeChildInterpreter(started=False, verbose=False)
    interpreter = _FakeParentInterpreter(child)
    doc_text = "x" * 100_001
    seen_contexts: list[str] = []

    def _failing_write(path: str, content: str) -> str:
        _ = path, content
        raise OSError("sandbox write unavailable")

    child.session.write_file = _failing_write

    def _mock_build(**kwargs: Any) -> Any:
        def _module(**kw: Any) -> dspy.Prediction:
            seen_contexts.append(str(kw.get("context", "")))
            return dspy.Prediction(answer="fallback result")

        return _module

    monkeypatch.setattr(
        document_tools,
        "fetch_document_text",
        lambda url: {"status": "ok", "text": doc_text, "char_count": len(doc_text)},
    )
    monkeypatch.setattr(rlm_delegate_mod, "build_recursive_subquery_rlm", _mock_build)

    result = rlm_delegate_mod.delegate_to_rlm(
        "read the document",
        document_url="https://example.com/doc.txt",
        interpreter=interpreter,
    )

    assert result["status"] == "ok"
    assert (
        "truncated after 100000 chars because sandbox staging failed"
        in seen_contexts[0]
    )
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

    result = rlm_delegate_mod.delegate_to_rlm(
        "Inspect the codebase implementation for sandbox budget session restore",
        interpreter=interpreter,
    )

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

    result = rlm_delegate_mod.delegate_to_rlm(
        "Inspect the codebase implementation for sandbox budget session restore",
        interpreter=interpreter,
    )

    assert result["status"] == "ok"
    assert child.session.write_calls == []
    assert "local_workspace_snapshot.txt" not in seen_contexts[0]


def test_delegate_to_rlm_skips_local_workspace_snapshot_for_volume_children(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Volume-backed child sandboxes already have mounted workspace context."""
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
    child.volume_name = "workspace-volume"
    interpreter = _FakeParentInterpreter(child)
    seen_contexts: list[str] = []

    def _mock_build(**kwargs: Any) -> Any:
        def _module(**kw: Any) -> dspy.Prediction:
            seen_contexts.append(str(kw.get("context", "")))
            return dspy.Prediction(answer="volume-backed answer")

        return _module

    monkeypatch.setattr(rlm_delegate_mod, "build_recursive_subquery_rlm", _mock_build)

    result = rlm_delegate_mod.delegate_to_rlm(
        "Inspect the codebase implementation for sandbox budget session restore",
        interpreter=interpreter,
    )

    assert result["status"] == "ok"
    assert child.session.write_calls == []
    assert "local_workspace_snapshot.txt" not in seen_contexts[0]


class _CapturingRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def store_rlm_trace(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _Identity:
    tenant_id = "tenant-x"
    workspace_id = "workspace-y"


def test_persist_child_trace_uses_head_tail_preview_for_long_answers() -> None:
    """Long child-RLM answers round-trip through head+tail truncation."""
    answer = "HEAD_MARKER" + ("A" * 3_000) + "TAIL_MARKER"

    repository = _CapturingRepository()
    interpreter = type(
        "Interp",
        (),
        {
            "_host_repository": repository,
            "_host_identity": _Identity(),
            "_host_run_id": "run-1",
        },
    )()

    rlm_delegate_mod._persist_child_trace(
        interpreter=interpreter,
        query="what?",
        answer=answer,
        prediction=type("P", (), {"trajectory": None})(),
        started_at=0.0,
    )

    assert len(repository.calls) == 1
    call = repository.calls[0]

    payload = call["payload_json"]
    preview = payload["answer_preview"]
    assert preview.startswith("HEAD_MARKER")
    assert preview.endswith("TAIL_MARKER")
    assert "characters omitted" in preview
    assert payload["answer_length"] == len(answer)

    # summary_text is the same preview string, not a head-only slice.
    assert call["summary_text"] == preview
