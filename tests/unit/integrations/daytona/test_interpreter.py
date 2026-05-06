from __future__ import annotations

import ast
import asyncio
import json
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from dspy.primitives import FinalOutput
from dspy.primitives.code_interpreter import CodeInterpreterError

from fleet_rlm.integrations.daytona.bridge import DaytonaBridgeExecution
from fleet_rlm.integrations.daytona.diagnostics import DaytonaDiagnosticError
from fleet_rlm.integrations.daytona.interpreter import (
    DaytonaInterpreter,
    RLMChildIsolationError,
)
from fleet_rlm.integrations.daytona.runtime import (
    DaytonaSandboxRuntime,
    DaytonaSandboxSession,
)
from fleet_rlm.integrations.daytona.sandbox_spec import SandboxSpec
from fleet_rlm.runtime.execution.interpreter_protocol import ExecutionProfile
from fleet_rlm.utils.sandbox_ownership import SANDBOX_OWNER_LABEL, sandbox_owner_labels

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
        envs: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> _FakeExecutionResult:
        del context, envs, timeout
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
    def __init__(self, sandbox_id: str = "sbx-123") -> None:
        self.id = sandbox_id
        self.code_interpreter = _FakeCodeInterpreter()
        self.fs = _FakeFs()
        self.process = _FakeProcess()
        self.delete_calls = 0

    def delete(self) -> None:
        self.delete_calls += 1
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
        self.fork_calls: list[tuple[str | None, str | None]] = []
        self.fail_next_resume: Exception | None = None
        self.fail_next_reconcile: Exception | None = None
        self.fail_next_fork: Exception | None = None
        self.last_spec: object | None = None

    async def acreate_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
        volume_name: str | None = None,
        spec: object | None = None,
    ) -> DaytonaSandboxSession:
        self.last_spec = spec
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

    def fork_sandbox(
        self,
        session: DaytonaSandboxSession,
        *,
        name: str | None = None,
        timeout: float = 60.0,
    ) -> DaytonaSandboxSession:
        del timeout
        self.fork_calls.append((session.sandbox_id, name))
        if self.fail_next_fork is not None:
            exc = self.fail_next_fork
            self.fail_next_fork = None
            raise exc
        return DaytonaSandboxSession(
            sandbox=_FakeSandbox("sbx-fork"),
            repo_url=session.repo_url,
            ref=session.ref,
            volume_name=session.volume_name,
            workspace_path=session.workspace_path,
            context_sources=list(session.context_sources),
            volume_mount_path=session.volume_mount_path,
            context_id=None,
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


def test_daytona_interpreter_default_execution_profile_updates_executor() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)
    events: list[dict[str, Any]] = []
    interpreter.execution_event_callback = events.append

    interpreter.default_execution_profile = ExecutionProfile.ROOT_INTERLOCUTOR
    interpreter.execute("counter = 2")

    assert events[0]["execution_profile"] == ExecutionProfile.ROOT_INTERLOCUTOR.value


def test_daytona_interpreter_strips_trailing_dspy_completed_marker() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)

    result = interpreter.execute("SUBMIT(answer='ok')\n]] [[ ## completed ## ]]")

    assert isinstance(result, FinalOutput)
    assert getattr(result, "output") == {"answer": "ok"}
    executed = runtime.session.sandbox.code_interpreter.run_calls[-1]
    assert "[[ ## completed ## ]]" not in executed
    assert "]]" not in executed


def test_daytona_interpreter_raises_on_bad_marker_leak() -> None:
    """CodeSanitizationError from prepare_execution_code must surface as CodeInterpreterError.

    Previously the error was serialised to a string and returned; that was
    incorrect because coerce_sandbox_result would then normalise it back to
    {"status": "ok", …}, silently masking the failure.
    """
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)

    with pytest.raises(
        CodeInterpreterError, match="Unable to prepare executable Python|SyntaxError"
    ):
        interpreter.execute("if True:\n    pass\nelse\n[[ ## completed ## ]]")


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
        "fleet_rlm.integrations.daytona.interpreter.DaytonaToolBridge",
        _FakeBridge,
    )

    result = interpreter.execute("answer = llm_query('hello')\nSUBMIT(answer=answer)")

    assert isinstance(result, FinalOutput)
    assert getattr(result, "output") == {"answer": "HOST:hello"}
    assert "llm_query" in captured["tools"]


def test_daytona_interpreter_bridge_detection_uses_cleaned_code(monkeypatch) -> None:
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
            del timeout, on_stdout, on_stderr
            captured["code"] = code
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
        "fleet_rlm.integrations.daytona.interpreter.DaytonaToolBridge",
        _FakeBridge,
    )

    result = interpreter.execute(
        "Code:\n```python\nanswer = llm_query('hello')\nSUBMIT(answer=answer)\n```\n[[ ## completed ## ]]"
    )

    assert isinstance(result, FinalOutput)
    assert getattr(result, "output") == {"answer": "HOST:hello"}
    assert "[[ ## completed ## ]]" not in captured["code"]
    assert "```" not in captured["code"]


def test_daytona_interpreter_bridge_injection_error_propagates(monkeypatch) -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)

    class _FakeBridge:
        def __init__(self, *, sandbox: Any, context: Any) -> None:
            del sandbox, context

        def bind_context(self, context: Any) -> None:
            del context

        async def async_tools(self, tools: dict[str, Any]) -> None:
            del tools
            raise CodeInterpreterError(
                "Failed to inject tool 'store_evidence': invalid syntax"
            )

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaToolBridge",
        _FakeBridge,
    )

    with pytest.raises(CodeInterpreterError, match="store_evidence"):
        interpreter.execute("answer = llm_query('hello')\nSUBMIT(answer=answer)")


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


def test_daytona_interpreter_applies_owner_labels_to_created_sandbox_spec() -> None:
    runtime = _FakeRuntime()
    owner_labels = sandbox_owner_labels(
        tenant_claim="tenant-a",
        user_claim="user-a",
        session_id="session-a",
    )
    interpreter = DaytonaInterpreter(
        runtime=runtime,
        volume_name="tenant-a",
        sandbox_spec=SandboxSpec(
            labels={
                "env": "test",
                SANDBOX_OWNER_LABEL: "untrusted-owner",
            }
        ),
        sandbox_labels=owner_labels,
    )

    interpreter.start()

    assert isinstance(runtime.last_spec, SandboxSpec)
    assert runtime.last_spec.volume_name == "tenant-a"
    assert runtime.last_spec.labels is not None
    assert runtime.last_spec.labels["env"] == "test"
    assert (
        runtime.last_spec.labels[SANDBOX_OWNER_LABEL]
        == owner_labels[SANDBOX_OWNER_LABEL]
    )


def test_daytona_interpreter_resumes_session_when_loop_owner_changes() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)
    interpreter.start()

    runtime.session.owner_thread_id = -1
    runtime.session.owner_loop_id = -1

    ensured = interpreter._ensure_session_sync()

    assert ensured is runtime.session
    # context_id is cleared on loop-owner mismatch so a fresh interpreter
    # context is created on the current event loop (prevents "Future attached
    # to a different loop" errors in child dspy.RLM modules).
    assert runtime.resume_calls == [("sbx-123", None)]
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


def test_invoke_tool_prefers_fleet_shared_llm_query_budget() -> None:
    """Fleet-owned llm_query callbacks must bypass dspy.RLM's fresh counters."""
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)

    injected_calls: list[str] = []
    fleet_calls: list[str] = []

    def injected_llm_query(prompt: str) -> str:
        injected_calls.append(prompt)
        return "injected"

    def fleet_llm_query(prompt: str) -> str:
        fleet_calls.append(prompt)
        return "fleet"

    interpreter.tools = {"llm_query": injected_llm_query}
    interpreter.llm_query = fleet_llm_query  # type: ignore[method-assign]

    bridge = interpreter._bridge_tools()
    assert bridge["llm_query"] is injected_llm_query

    result = interpreter._invoke_tool("llm_query", ["hello"], {})
    assert result == "fleet"
    assert fleet_calls == ["hello"]
    assert injected_calls == []


def test_invoke_tool_prefers_fleet_shared_llm_query_batched_budget() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)

    injected_calls: list[list[str]] = []
    fleet_calls: list[list[str]] = []

    def injected_llm_query_batched(prompts: list[str]) -> list[str]:
        injected_calls.append(prompts)
        return ["injected"]

    def fleet_llm_query_batched(prompts: list[str]) -> list[str]:
        fleet_calls.append(prompts)
        return [f"fleet:{prompt}" for prompt in prompts]

    interpreter.tools = {"llm_query_batched": injected_llm_query_batched}
    interpreter.llm_query_batched = fleet_llm_query_batched  # type: ignore[method-assign]

    result = interpreter._invoke_tool("llm_query_batched", [["a", "b"]], {})

    assert result == ["fleet:a", "fleet:b"]
    assert fleet_calls == [["a", "b"]]
    assert injected_calls == []


def test_invoke_tool_dispatches_bridged_sub_rlm() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)
    calls: list[tuple[str, str]] = []

    def fake_sub_rlm(prompt: str, context: str = "") -> str:
        calls.append((prompt, context))
        return "child answer"

    interpreter.sub_rlm = fake_sub_rlm  # type: ignore[method-assign]

    result = interpreter._invoke_tool(
        "sub_rlm",
        ["solve this"],
        {"context": "parent context"},
    )

    assert result == "child answer"
    assert calls == [("solve this", "parent context")]


def test_invoke_tool_dispatches_bridged_sub_rlm_batched() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)
    calls: list[tuple[list[str], str]] = []

    def fake_sub_rlm_batched(prompts: list[str], context: str = "") -> list[str]:
        calls.append((prompts, context))
        return [f"answer:{prompt}" for prompt in prompts]

    interpreter.sub_rlm_batched = fake_sub_rlm_batched  # type: ignore[method-assign]

    result = interpreter._invoke_tool(
        "sub_rlm_batched",
        [["a", "b"]],
        {"context": "ctx"},
    )

    assert result == ["answer:a", "answer:b"]
    assert calls == [(["a", "b"], "ctx")]


def test_sub_rlm_sandbox_code_is_routed_through_bridge() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)

    bridge = interpreter._bridge_tools()

    assert "sub_rlm" in bridge
    assert interpreter._requires_bridge("answer = sub_rlm('hello')", bridge)
    assert not interpreter._requires_bridge("answer = 1 + 1", bridge)


def test_bridge_detection_ignores_callback_names_in_comments_and_strings() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)

    bridge = interpreter._bridge_tools()
    code = "# sub_rlm('not a call')\ntext = \"llm_query('also not a call')\""

    assert not interpreter._requires_bridge(code, bridge)
    interpreter._reject_unsupported_recursive_callbacks("text = \"rlm_query('nope')\"")


def test_bridge_detection_handles_attribute_callback_calls() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)

    bridge = interpreter._bridge_tools()

    assert interpreter._requires_bridge("callbacks.sub_rlm('hello')", bridge)


def test_bridge_tools_falls_back_to_interpreter_llm_query() -> None:
    """When no dspy.RLM injection has happened, bridge_tools falls back to
    the interpreter's LLMQueryMixin.llm_query method."""
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)

    # No injection — _tools should be empty
    bridge = interpreter._bridge_tools()
    assert bridge["llm_query"] == interpreter.llm_query
    assert bridge["llm_query_batched"] == interpreter.llm_query_batched


def test_invoke_tool_dispatches_bridged_fetch_document_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)
    calls: list[str] = []

    def fake_fetch_document_text(url_or_path: str) -> dict[str, Any]:
        calls.append(url_or_path)
        return {"status": "ok", "text": "body", "char_count": 4, "metadata": {}}

    monkeypatch.setattr(
        "fleet_rlm.runtime.tools.document_tools.fetch_document_text",
        fake_fetch_document_text,
    )

    result = interpreter._invoke_tool(
        "fetch_document_text", ["https://example.test"], {}
    )

    assert result == {
        "status": "ok",
        "text": "body",
        "char_count": 4,
        "metadata": {},
    }
    assert calls == ["https://example.test"]


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


def test_daytona_interpreter_does_not_recreate_open_owned_runtime() -> None:
    runtime = object.__new__(DaytonaSandboxRuntime)
    runtime._resolved_config = SimpleNamespace()
    runtime._client = None

    interpreter = DaytonaInterpreter(runtime=runtime, owns_runtime=True)

    interpreter._ensure_runtime_available()

    assert interpreter.runtime is runtime
    assert interpreter._runtime_closed is False


def test_daytona_interpreter_shutdown_deletes_child_context_without_deleting_sandbox() -> (
    None
):
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(
        runtime=runtime,
        delete_session_on_shutdown=False,
        delete_context_on_shutdown=True,
    )

    delete_context_calls = 0
    close_driver_calls = 0
    delete_calls = 0

    async def _adelete_context() -> None:
        nonlocal delete_context_calls
        delete_context_calls += 1

    async def _aclose_driver() -> None:
        nonlocal close_driver_calls
        close_driver_calls += 1

    async def _adelete() -> None:
        nonlocal delete_calls
        delete_calls += 1

    fake_session = SimpleNamespace(
        sandbox_id="sbx-child",
        workspace_path="/workspace/repo",
        context_sources=[],
        context_id="ctx-child",
        volume_name=None,
        adelete_context=_adelete_context,
        aclose_driver=_aclose_driver,
        adelete=_adelete,
    )
    interpreter._session = fake_session  # type: ignore[assignment]

    interpreter.shutdown()

    assert delete_context_calls == 1
    assert close_driver_calls == 0
    assert delete_calls == 0


def test_daytona_interpreter_auto_child_forks_when_no_volume() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)
    interpreter._session = runtime.session
    parent_sandbox = runtime.session.sandbox

    child = interpreter.build_delegate_child(remaining_llm_budget=10)

    assert isinstance(child, DaytonaInterpreter)
    assert child is not interpreter
    assert child._session is not None
    assert child._session.sandbox is not parent_sandbox
    assert child._session.sandbox_id == "sbx-fork"
    assert child._session.context_id is None
    assert child.delete_session_on_shutdown is True
    assert runtime.fork_calls
    assert child.child_isolation_metadata == {
        "mode": "auto",
        "strategy": "fork",
        "parent_sandbox_id": "sbx-123",
        "child_sandbox_id": "sbx-fork",
    }


def test_daytona_interpreter_context_mode_reuses_parent_session() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime, child_isolation_mode="context")
    interpreter._session = runtime.session
    parent_sandbox = runtime.session.sandbox

    child = interpreter.build_delegate_child(remaining_llm_budget=10)

    assert isinstance(child, DaytonaInterpreter)
    assert child._session is not None
    assert child._session.sandbox is parent_sandbox
    assert child._session.context_id is None
    assert child.delete_context_on_shutdown is True
    assert child.delete_session_on_shutdown is False
    assert runtime.fork_calls == []
    assert child.child_isolation_metadata == {
        "mode": "context",
        "strategy": "context",
        "parent_sandbox_id": "sbx-123",
        "child_sandbox_id": "sbx-123",
    }


def test_daytona_interpreter_auto_child_uses_clean_subpath_with_volume() -> None:
    runtime = _FakeRuntime()
    runtime.session.volume_name = "workspace-volume"
    interpreter = DaytonaInterpreter(runtime=runtime, volume_name="workspace-volume")
    interpreter._session = runtime.session

    child = interpreter.build_delegate_child(remaining_llm_budget=10)

    assert isinstance(child, DaytonaInterpreter)
    assert child._session is None
    assert child.volume_name == "workspace-volume"
    assert child.volume_subpath is not None
    assert child.volume_subpath.startswith("meta/rlm-children/sbx-123/")
    assert child.delete_session_on_shutdown is True
    assert child.delete_context_on_shutdown is False
    assert runtime.fork_calls == []
    assert child.child_isolation_metadata is not None
    assert child.child_isolation_metadata["strategy"] == "clean"
    assert child.child_isolation_metadata["reason"] == "durable_volume_mounted"
    assert child.child_isolation_metadata["volume_name"] == "workspace-volume"
    assert child.child_isolation_metadata["volume_subpath"] == child.volume_subpath


def test_daytona_interpreter_fork_failure_retries_clean_child() -> None:
    runtime = _FakeRuntime()
    runtime.fail_next_fork = RuntimeError("fork unavailable")
    interpreter = DaytonaInterpreter(runtime=runtime)
    interpreter._session = runtime.session

    child = interpreter.build_delegate_child(remaining_llm_budget=10)

    assert isinstance(child, DaytonaInterpreter)
    assert runtime.fork_calls
    assert child._session is None
    assert child.volume_name is None
    assert child.volume_subpath is None
    assert child.child_isolation_metadata is not None
    assert child.child_isolation_metadata["strategy"] == "clean"
    assert child.child_isolation_metadata["reason"] == "fork_failed"
    assert child.child_isolation_metadata["fallback_from"] == "fork"
    assert child.child_isolation_metadata["fallback_status"] == "used"


def test_daytona_interpreter_fork_failure_can_fail_closed() -> None:
    runtime = _FakeRuntime()
    runtime.fail_next_fork = RuntimeError("fork unavailable")
    interpreter = DaytonaInterpreter(runtime=runtime, child_fork_fallback="fail")
    interpreter._session = runtime.session

    with pytest.raises(RLMChildIsolationError) as exc_info:
        interpreter.build_delegate_child(remaining_llm_budget=10)

    assert exc_info.value.metadata["strategy"] == "fork"
    assert exc_info.value.metadata["fallback_status"] == "disabled"
    assert "fork unavailable" in exc_info.value.metadata["error"]


def test_daytona_interpreter_child_shutdown_deletes_forked_sandbox() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)
    interpreter._session = runtime.session

    child = interpreter.build_delegate_child(remaining_llm_budget=10)
    forked_sandbox = child._session.sandbox

    child.shutdown()

    assert forked_sandbox.delete_calls == 1
    assert child._session is None
    assert child._persisted_sandbox_id is None


def test_daytona_interpreter_child_inherits_parent_sandbox_labels() -> None:
    runtime = _FakeRuntime()
    parent_labels = {"team": "alpha", "env": "prod"}
    interpreter = DaytonaInterpreter(
        runtime=runtime,
        sandbox_labels=parent_labels,
    )
    interpreter._session = runtime.session

    child = interpreter.build_delegate_child(remaining_llm_budget=10)

    assert child.sandbox_labels == parent_labels


def test_daytona_interpreter_safe_variables_handles_circular_refs() -> None:
    runtime = _FakeRuntime()
    interpreter = DaytonaInterpreter(runtime=runtime)

    circular_dict: dict[str, Any] = {"name": "root"}
    circular_dict["self"] = circular_dict

    result = interpreter.safe_variables({"a": 1, "b": circular_dict})

    assert result["a"] == 1
    assert isinstance(result["b"], str)
    assert "root" in result["b"]
