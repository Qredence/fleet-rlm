from __future__ import annotations

import asyncio
import ast
import json
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from dspy.primitives import FinalOutput
from dspy.primitives.code_interpreter import CodeInterpreterError

from fleet_rlm.integrations.providers.daytona.bridge import DaytonaBridgeExecution
from fleet_rlm.integrations.providers.daytona.diagnostics import DaytonaDiagnosticError
from fleet_rlm.integrations.providers.daytona.interpreter import DaytonaInterpreter
from fleet_rlm.integrations.providers.daytona.runtime import DaytonaSandboxSession

_FINAL_OUTPUT_MARKER = "__DSPY_FINAL_OUTPUT__"


class _FakeExecutionResult:
    def __init__(
        self, *, stdout: str = "", stderr: str = "", error: Any = None
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.error = error


class _FakeCodeInterpreter:
    def __init__(self) -> None:
        self.contexts: list[Any] = []
        self.run_calls: list[str] = []
        self.submit_mode = "generic"
        self.list_contexts_error: Exception | None = None

    def create_context(self, cwd: str | None = None) -> Any:
        context = SimpleNamespace(id=f"ctx-{len(self.contexts) + 1}", cwd=cwd)
        self.contexts.append(context)
        return context

    def list_contexts(self) -> list[Any]:
        if self.list_contexts_error is not None:
            exc = self.list_contexts_error
            self.list_contexts_error = None
            raise exc
        return list(self.contexts)

    def delete_context(self, context: Any) -> None:
        self.contexts = [item for item in self.contexts if item is not context]

    def run_code(
        self,
        code: str,
        *,
        context: Any | None = None,
        on_stdout=None,
        on_stderr=None,
        timeout: int | None = None,
    ) -> _FakeExecutionResult:
        del context, timeout
        self.run_calls.append(code)
        if "def SUBMIT(**kwargs)" in code:
            self.submit_mode = "generic"
            return _FakeExecutionResult()
        if "def SUBMIT(" in code:
            self.submit_mode = "typed"
            return _FakeExecutionResult()
        if "_FINAL_OUTPUT_MARKER" in code:
            return _FakeExecutionResult()
        if "counter += 3" in code:
            payload = f"{_FINAL_OUTPUT_MARKER}{json.dumps({'output': 5})}{_FINAL_OUTPUT_MARKER}"
            if on_stdout is not None:
                on_stdout(SimpleNamespace(output=payload))
            return _FakeExecutionResult(stdout=payload)
        if "SUBMIT(" in code:
            if self.submit_mode == "typed" and any(
                f"{field}=" in code
                for field in (
                    "status",
                    "result",
                    "error",
                    "stdout",
                    "stderr",
                    "ok",
                    "path",
                    "content",
                    "chars",
                    "process_id",
                    "message",
                    "logs",
                    "ast",
                )
            ):
                return _FakeExecutionResult(
                    error=SimpleNamespace(
                        name="TypeError",
                        value="SUBMIT() got an unexpected keyword argument 'result'",
                    )
                )
            payload_dict = _submit_payload(code)
            payload = (
                f"{_FINAL_OUTPUT_MARKER}"
                f"{json.dumps(payload_dict, ensure_ascii=False)}"
                f"{_FINAL_OUTPUT_MARKER}"
            )
            if on_stdout is not None:
                on_stdout(SimpleNamespace(output=payload))
            return _FakeExecutionResult(
                stdout=payload,
                error=SimpleNamespace(name="_FleetFinalOutput", value="submitted"),
            )
        return _FakeExecutionResult()


def _submit_payload(code: str) -> dict[str, Any]:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "SUBMIT":
            payload: dict[str, Any] = {}
            for keyword in node.keywords:
                if keyword.arg is None:
                    continue
                try:
                    payload[keyword.arg] = ast.literal_eval(keyword.value)
                except Exception:
                    payload[keyword.arg] = f"<expr:{keyword.arg}>"
            return payload
    return {}


class _FakeFs:
    def __init__(self) -> None:
        self.uploads: dict[str, bytes] = {}

    def upload_file(self, data: bytes, path: str) -> None:
        self.uploads[path] = bytes(data)

    def download_file(self, path: str) -> bytes:
        return self.uploads.get(path, b"")

    def list_files(self, path: str) -> list[Any]:
        del path
        return []


class _FakeProcess:
    def delete_session(self, session_id: str) -> None:
        del session_id
        return None


class _FakeSandbox:
    def __init__(self) -> None:
        self.id = "sbx-123"
        self.code_interpreter = _FakeCodeInterpreter()
        self.fs = _FakeFs()
        self.process = _FakeProcess()

    def delete(self) -> None:
        return None


class _FakeRuntime:
    def __init__(self) -> None:
        self._resolved_config = SimpleNamespace()
        self.session = DaytonaSandboxSession(
            sandbox=_FakeSandbox(),
            repo_url="https://github.com/example/repo.git",
            ref="main",
            volume_name=None,
            workspace_path="/workspace/repo",
            context_sources=[],
        )
        self.create_calls: list[
            tuple[str | None, str | None, list[str], str | None]
        ] = []
        self.resume_calls: list[tuple[str, str | None]] = []
        self.reconcile_calls: list[tuple[str | None, str | None, list[str]]] = []
        self.fail_next_resume: Exception | None = None
        self.fail_next_reconcile: Exception | None = None

    async def acreate_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
        volume_name: str | None = None,
        spec: object | None = None,
    ) -> DaytonaSandboxSession:
        self.create_calls.append(
            (repo_url, ref, list(context_paths or []), volume_name)
        )
        self.session.repo_url = repo_url
        self.session.ref = ref
        self.session.volume_name = volume_name
        self.session.owner_thread_id = threading.get_ident()
        self.session.owner_loop_id = id(asyncio.get_running_loop())
        workspace_name = (
            str(repo_url or "").rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
            or "repo"
        )
        self.session.workspace_path = f"/workspace/{workspace_name}"
        del context_paths
        return self.session

    async def aresume_workspace_session(
        self,
        *,
        sandbox_id: str,
        repo_url: str | None,
        ref: str | None,
        volume_name: str | None = None,
        workspace_path: str,
        context_sources: list[Any] | None = None,
        context_id: str | None = None,
    ) -> DaytonaSandboxSession:
        self.resume_calls.append((sandbox_id, context_id))
        if self.fail_next_resume is not None:
            exc = self.fail_next_resume
            self.fail_next_resume = None
            raise exc
        self.session.repo_url = repo_url
        self.session.ref = ref
        self.session.volume_name = volume_name
        self.session.workspace_path = workspace_path
        self.session.owner_thread_id = threading.get_ident()
        self.session.owner_loop_id = id(asyncio.get_running_loop())
        del context_sources
        return self.session

    async def areconcile_workspace_session(
        self,
        session: DaytonaSandboxSession,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
    ) -> DaytonaSandboxSession:
        self.reconcile_calls.append((repo_url, ref, list(context_paths or [])))
        if self.fail_next_reconcile is not None:
            exc = self.fail_next_reconcile
            self.fail_next_reconcile = None
            raise exc
        session.repo_url = repo_url
        session.ref = ref
        session.context_sources = []
        session.owner_thread_id = threading.get_ident()
        session.owner_loop_id = id(asyncio.get_running_loop())
        if repo_url:
            session.workspace_path = "/workspace/reconfigured"
        return session

    def reconcile_workspace_session(
        self,
        session: DaytonaSandboxSession,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
    ) -> DaytonaSandboxSession:
        raise AssertionError(
            "internal Daytona flow should use areconcile_workspace_session"
        )


def test_daytona_interpreter_execute_direct_reuses_context_and_returns_final_output() -> (
    None
):
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(
        runtime=runtime,
        repo_url="https://github.com/example/repo.git",
        repo_ref="main",
    )

    first = interpreter.execute("counter = 2")
    second = interpreter.execute("counter += 3\nSUBMIT(output=counter)")

    assert first == ""
    assert isinstance(second, FinalOutput)
    assert getattr(second, "output") == {"output": 5}
    assert len(runtime.session.sandbox.code_interpreter.contexts) == 1


def test_daytona_interpreter_uses_bridge_for_llm_queries(monkeypatch) -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)
    interpreter.llm_query = lambda prompt: f"HOST:{prompt}"  # type: ignore[method-assign]

    captured: dict[str, Any] = {}

    class _FakeBridge:
        def __init__(self, *, sandbox: Any, context: Any) -> None:
            captured["sandbox"] = sandbox
            captured["context"] = context

        def bind_context(self, context: Any) -> None:
            captured["bound_context"] = context

        async def async_tools(self, tools: dict[str, Any]) -> None:
            captured["tools"] = dict(tools)

        async def aexecute(
            self,
            *,
            code: str,
            timeout: int,
            tool_executor,
            on_stdout=None,
            on_stderr=None,
        ):
            del on_stdout, on_stderr
            captured["code"] = code
            captured["timeout"] = timeout
            answer = tool_executor("llm_query", ["hello"], {})
            payload = f"{_FINAL_OUTPUT_MARKER}{json.dumps({'answer': answer})}{_FINAL_OUTPUT_MARKER}"
            return DaytonaBridgeExecution(
                result=_FakeExecutionResult(),
                stdout=payload,
                stderr="",
                callback_count=1,
            )

        async def aclose(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(
        "fleet_rlm.integrations.providers.daytona.interpreter.DaytonaToolBridge",
        _FakeBridge,
    )

    result = interpreter.execute("answer = llm_query('hello')\nSUBMIT(answer=answer)")

    assert isinstance(result, FinalOutput)
    assert getattr(result, "output") == {"answer": "HOST:hello"}
    assert "llm_query" in captured["tools"]


def test_daytona_interpreter_exports_context_id_for_resume() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)
    interpreter.start()

    exported = interpreter.export_session_state()

    restored = DaytonaInterpreter(runtime=runtime)
    restored.import_session_state(exported)
    restored.start()

    assert runtime.resume_calls == [("sbx-123", "ctx-1")]


def test_daytona_interpreter_resumed_session_recreates_context_when_persisted_one_is_stale() -> (
    None
):
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)
    interpreter.start()

    exported = interpreter.export_session_state()

    restored = DaytonaInterpreter(runtime=runtime)
    restored.import_session_state(exported)
    runtime.session._context = None
    runtime.session.sandbox.code_interpreter.list_contexts_error = RuntimeError(
        "stale context cache"
    )
    restored.start()

    assert runtime.resume_calls == [("sbx-123", "ctx-1")]
    assert runtime.session.context_id == "ctx-2"
    assert restored.export_session_state()["daytona"]["context_id"] == "ctx-2"
    assert restored._last_sandbox_transition == "resumed"


def test_daytona_interpreter_restores_generic_submit_after_typed_execution() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)
    interpreter.output_fields = [{"name": "answer", "type": "str"}]

    typed = interpreter.execute("SUBMIT(answer='typed')\n")
    interpreter.output_fields = None
    generic = interpreter.execute(
        "SUBMIT(status='ok', result='saved', path='workspace/out.txt')\n"
    )

    assert isinstance(typed, FinalOutput)
    assert getattr(typed, "output") == {"answer": "typed"}
    assert isinstance(generic, FinalOutput)
    assert getattr(generic, "output") == {
        "status": "ok",
        "result": "saved",
        "path": "workspace/out.txt",
    }
    run_calls = runtime.session.sandbox.code_interpreter.run_calls
    assert any("def SUBMIT(answer: str):" in call for call in run_calls)
    assert any("def SUBMIT(**kwargs):" in call for call in run_calls)


def test_daytona_interpreter_reconciles_workspace_without_recreating_session() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(
        runtime=runtime,
        repo_url="https://github.com/example/repo.git",
        repo_ref="main",
        context_paths=["docs/a.md"],
        volume_name="tenant-a",
    )
    interpreter.start()
    active_session = interpreter._session
    assert active_session is runtime.session

    interpreter.configure_workspace(
        repo_url="https://github.com/example/other.git",
        repo_ref="develop",
        context_paths=["docs/b.md"],
        volume_name="tenant-a",
    )

    ensured = interpreter._ensure_session_sync()

    assert ensured is active_session
    assert runtime.reconcile_calls == [
        ("https://github.com/example/other.git", "develop", ["docs/b.md"])
    ]
    assert interpreter._last_sandbox_transition == "reused"
    assert interpreter._last_workspace_reconfigured is True


def test_daytona_interpreter_resumes_session_when_loop_owner_changes() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)
    interpreter.start()

    runtime.session.owner_thread_id = -1
    runtime.session.owner_loop_id = -1

    ensured = interpreter._ensure_session_sync()

    assert ensured is runtime.session
    assert runtime.resume_calls == [("sbx-123", "ctx-1")]
    assert interpreter._last_sandbox_transition == "resumed"
    assert interpreter._last_workspace_reconfigured is False


def test_daytona_interpreter_marks_reconcile_recreate_fallback_as_degraded() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(
        runtime=runtime,
        repo_url="https://github.com/example/repo.git",
        repo_ref="main",
        context_paths=["docs/a.md"],
        volume_name="tenant-a",
    )
    interpreter.start()
    runtime.fail_next_reconcile = DaytonaDiagnosticError(
        "reconcile failed",
        category="sandbox_create_clone_error",
        phase="repo_clone",
    )

    interpreter.configure_workspace(
        repo_url="https://github.com/example/other.git",
        repo_ref="develop",
        context_paths=["docs/b.md"],
        volume_name="tenant-a",
    )
    interpreter._ensure_session_sync()

    assert runtime.reconcile_calls == [
        ("https://github.com/example/other.git", "develop", ["docs/b.md"])
    ]
    assert len(runtime.create_calls) == 2
    assert interpreter._last_sandbox_transition == "recreated"
    assert interpreter.current_runtime_metadata() == {
        "sandbox_active": True,
        "workspace_reconfigured": False,
        "runtime_degraded": True,
        "runtime_fallback_used": True,
        "sandbox_id": "sbx-123",
        "workspace_path": "/workspace/other",
        "volume_name": "tenant-a",
        "sandbox_transition": "recreated",
        "runtime_failure_category": "sandbox_create_clone_error",
        "runtime_failure_phase": "repo_clone",
    }


def test_daytona_interpreter_marks_resume_recreate_fallback_as_degraded() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)
    interpreter.start()

    exported = interpreter.export_session_state()

    restored = DaytonaInterpreter(runtime=runtime)
    restored.import_session_state(exported)
    runtime.fail_next_resume = DaytonaDiagnosticError(
        "resume failed",
        category="sandbox_resume_error",
        phase="sandbox_resume",
    )

    restored.start()

    assert runtime.resume_calls == [("sbx-123", "ctx-1")]
    assert len(runtime.create_calls) == 2
    assert restored._last_sandbox_transition == "recreated"
    assert restored.current_runtime_metadata() == {
        "sandbox_active": True,
        "workspace_reconfigured": False,
        "runtime_degraded": True,
        "runtime_fallback_used": True,
        "sandbox_id": "sbx-123",
        "workspace_path": "/workspace/repo",
        "sandbox_transition": "recreated",
        "runtime_failure_category": "sandbox_resume_error",
        "runtime_failure_phase": "sandbox_resume",
    }


def test_daytona_interpreter_skips_bridge_injection_for_native_sandbox_tools() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)
    interpreter.tools = {
        "run": lambda command: {"command": command},
        "workspace_write": lambda path, content: {"path": path, "content": content},
        "workspace_read": lambda path: {"path": path},
        "custom_tool": lambda value: value,
    }

    bridge_tools = interpreter._bridge_tools()

    assert "run" not in bridge_tools
    assert "workspace_write" not in bridge_tools
    assert "workspace_read" not in bridge_tools
    assert "custom_tool" in bridge_tools
    assert "llm_query" in bridge_tools
    assert "rlm_query" not in bridge_tools
    assert "rlm_query_batched" not in bridge_tools


def test_daytona_interpreter_rejects_recursive_rlm_query_in_sandbox_code() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)

    with pytest.raises(CodeInterpreterError, match="agent-level only"):
        interpreter.execute("answer = rlm_query('hello')\nSUBMIT(answer=answer)")


def test_daytona_interpreter_rejects_recursive_rlm_query_batched_in_sandbox_code() -> (
    None
):
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)

    with pytest.raises(CodeInterpreterError, match="agent-level only"):
        interpreter.execute(
            "answers = rlm_query_batched([{'query': 'hello'}])\nSUBMIT(answer=answers)"
        )


def test_bridge_tools_prefers_dspy_rlm_injected_llm_query() -> None:
    """When dspy.RLM injects llm_query into interpreter._tools, the bridge
    should use that version (fresh counter per forward) rather than the
    interpreter's own LLMQueryMixin method."""
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)

    # Simulate dspy.RLM injection via interpreter.tools.update(...)
    calls: list[str] = []

    def injected_llm_query(prompt: str) -> str:
        calls.append(prompt)
        return "injected"

    interpreter.tools = {"llm_query": injected_llm_query}

    bridge = interpreter._bridge_tools()
    assert bridge["llm_query"] is injected_llm_query

    # invoke_tool should also use the injected version
    result = interpreter._invoke_tool("llm_query", ["hello"], {})
    assert result == "injected"
    assert calls == ["hello"]


def test_bridge_tools_falls_back_to_interpreter_llm_query() -> None:
    """When no dspy.RLM injection has happened, bridge_tools falls back to
    the interpreter's LLMQueryMixin.llm_query method."""
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)

    # No injection — _tools should be empty
    bridge = interpreter._bridge_tools()
    assert bridge["llm_query"] == interpreter.llm_query
    assert bridge["llm_query_batched"] == interpreter.llm_query_batched


def test_daytona_interpreter_shutdown_closes_owned_runtime() -> None:
    runtime = _FakeRuntime()
    runtime.closed = 0

    async def _aclose() -> None:
        runtime.closed += 1

    runtime.aclose = _aclose  # type: ignore[attr-defined]

    interpreter = DaytonaInterpreter(runtime=runtime, owns_runtime=True)

    interpreter.shutdown()
    interpreter.shutdown()

    assert runtime.closed == 1
