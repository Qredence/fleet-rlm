from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from dspy.primitives import FinalOutput

from fleet_rlm.integrations.providers.daytona.bridge import DaytonaBridgeExecution
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

    def create_context(self, cwd: str | None = None) -> Any:
        context = SimpleNamespace(id=f"ctx-{len(self.contexts) + 1}", cwd=cwd)
        self.contexts.append(context)
        return context

    def list_contexts(self) -> list[Any]:
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
        if "def SUBMIT" in code or "_FINAL_OUTPUT_MARKER" in code:
            return _FakeExecutionResult()
        if "counter += 3" in code:
            payload = f"{_FINAL_OUTPUT_MARKER}{json.dumps({'output': 5})}{_FINAL_OUTPUT_MARKER}"
            if on_stdout is not None:
                on_stdout(SimpleNamespace(output=payload))
            return _FakeExecutionResult(stdout=payload)
        return _FakeExecutionResult()


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
            workspace_path="/workspace/repo",
            context_sources=[],
        )
        self.resume_calls: list[tuple[str, str | None]] = []

    def create_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
        volume_name: str | None = None,
    ) -> DaytonaSandboxSession:
        del repo_url, ref, context_paths, volume_name
        return self.session

    def resume_workspace_session(
        self,
        *,
        sandbox_id: str,
        repo_url: str | None,
        ref: str | None,
        workspace_path: str,
        context_sources: list[Any] | None = None,
        context_id: str | None = None,
    ) -> DaytonaSandboxSession:
        del repo_url, ref, workspace_path, context_sources
        self.resume_calls.append((sandbox_id, context_id))
        return self.session


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

        def sync_tools(self, tools: dict[str, Any]) -> None:
            captured["tools"] = dict(tools)

        def execute(
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

        def close(self) -> None:
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
