"""Daytona adapter behavior with an offline injectable backend."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


class _FakeBackend:
    """In-memory REPL stand-in for offline adapter tests."""

    def __init__(self) -> None:
        self.namespace: dict[str, object] = {"_out": ""}
        self.closed = False
        self.fail_with: BaseException | None = None
        self.calls = 0

    def run(self, code: str, variables: dict[str, object] | None = None) -> str:
        self.calls += 1
        if self.closed:
            msg = "backend already closed"
            raise RuntimeError(msg)
        if self.fail_with is not None:
            raise self.fail_with
        if variables:
            self.namespace.update(variables)
        exec(code, self.namespace, self.namespace)
        return str(self.namespace.get("_out", ""))

    def close(self) -> None:
        self.closed = True


def test_execute_returns_string_and_preserves_state() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter

    backend = _FakeBackend()
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.start()
    interp.execute("value = 41")
    result = interp.execute("_out = str(value + 1)")
    assert result == "42"


def test_invocation_installs_tools_output_and_context_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from uuid import uuid4

    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
    from fleet_rlm.rlm.events import observe_tool
    from fleet_rlm.rlm.output_contract import FleetOutputContract, OutputField
    from fleet_rlm.rlm.program import AttachmentContextCapsule, AttachmentContextEntry

    backend = InProcessInterpreterBackend()
    bind_tools = Mock(wraps=backend.bind_host_tools)
    submit = Mock(wraps=backend.ensure_submit)
    context = Mock(wraps=backend.bind_context_manifest)
    monkeypatch.setattr(backend, "bind_host_tools", bind_tools)
    monkeypatch.setattr(backend, "ensure_submit", submit)
    monkeypatch.setattr(backend, "bind_context_manifest", context)
    observed_wrapper = Mock(wraps=observe_tool)
    monkeypatch.setattr("fleet_rlm.daytona.interpreter.observe_tool", observed_wrapper)
    interp = DaytonaCodeInterpreter(backend=backend, tools={"configured": lambda: "configured"})
    interp.bind_observer(lambda _event: None)
    interp.tools.update({"llm_query": lambda prompt: prompt})
    interp.output_fields = [{"name": "answer", "type": "str"}]
    interp.bind_output_contract(FleetOutputContract((OutputField("answer", True),), max_output_chars=100))
    capsule = AttachmentContextCapsule(
        (AttachmentContextEntry(uuid4(), "input.txt", "text/plain", 1, "a" * 64, "/mnt/fleet/input.txt"),),
        mount_root="/mnt/fleet",
    )
    interp.bind_context_capsule(capsule)
    assert context.call_count == 0
    assert interp.execute("_out = configured()") == "configured"
    assert interp.execute("SUBMIT(answer=llm_query(prompt='second'))").output == {"answer": "second"}
    assert bind_tools.call_count == submit.call_count == context.call_count == observed_wrapper.call_count == 1
    submit.assert_called_once_with([{"name": "answer", "type": "str", "required": True}], 100)
    interp.shutdown()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda i: i.tools.__setitem__("late", lambda: "late"),
        lambda i: i.tools.__delitem__("tool"),
        lambda i: i.tools.clear(),
        lambda i: i.tools.update({"late": lambda: "late"}),
        lambda i: i.tools.pop("tool"),
        lambda i: i.tools.popitem(),
        lambda i: i.tools.setdefault("late", lambda: "late"),
        lambda i: i.tools.__ior__({"late": lambda: "late"}),
        lambda i: setattr(i, "output_fields", [{"name": "late", "type": "str"}]),
        lambda i: i.bind_output_contract(None),
        lambda i: i.bind_context_capsule(None),
        lambda i: i.bind_observer(None),
        lambda i: i.bind_turn_budget(None),
        lambda i: i.bind_turn_request("late"),
        lambda i: i.bind_async_bridge(None),
        lambda i: i.bind_tool_outcomes(tool_settled=None, tool_failed=None),
        lambda i: i.bind_run_scratch(None),
    ],
)
def test_post_seal_configuration_is_rejected_before_backend_action(mutate: Callable) -> None:
    from fleet_rlm.daytona.errors import DaytonaAdapterError
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter

    backend = _FakeBackend()
    interp = DaytonaCodeInterpreter(backend=backend, tools={"tool": lambda: "original"})
    interp.execute("value = 41")
    with pytest.raises(DaytonaAdapterError, match="sealed") as exc:
        mutate(interp)
    assert exc.value.cause_type == "InterpreterReuseError"
    assert backend.calls == 1
    assert set(interp.tools) == {"tool"}
    assert interp.execute("_out = str(value + 1)") == "42"
    interp.shutdown()


def test_failed_binding_installation_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.daytona.errors import DaytonaAdapterError
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    backend = InProcessInterpreterBackend()
    bind = Mock(side_effect=DaytonaAdapterError(message="binding failed", cause_type="InterpreterConfigurationError"))
    run = Mock(wraps=backend.run)
    monkeypatch.setattr(backend, "bind_host_tools", bind)
    monkeypatch.setattr(backend, "run", run)
    interp = DaytonaCodeInterpreter(backend=backend)
    for code in ("x = 1", "x = 2"):
        with pytest.raises(DaytonaAdapterError, match="binding failed"):
            interp.execute(code)
    assert bind.call_count == 1
    run.assert_not_called()
    interp.shutdown()
    assert backend.closed


def test_scratch_cleanup_remains_available_after_sealing() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter

    backend = _FakeBackend()
    backend.cleanup_run_scratch = Mock()
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.execute("value = 1")
    interp.cleanup_run_scratch()
    backend.cleanup_run_scratch.assert_called_once_with()
    interp.shutdown()


def test_run_backend_does_not_retry_typeerror_from_backend() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter

    class _TypeErrorBackend:
        def __init__(self) -> None:
            self.calls = 0

        def run(
            self,
            code: str,
            variables: dict[str, object] | None = None,
            *,
            on_stdout: Callable[[str], None] | None = None,
        ) -> str:
            del code, variables, on_stdout
            self.calls += 1
            raise TypeError("backend failed")

        def close(self) -> None:
            return None

    backend = _TypeErrorBackend()
    interpreter = DaytonaCodeInterpreter(backend=backend)

    with pytest.raises(TypeError, match="backend failed"):
        interpreter._run_backend("pass", None, on_stdout=lambda _value: None)

    assert backend.calls == 1


def test_run_backend_does_not_retry_typeerror_from_stdout_callback() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter

    class _CallbackBackend:
        def __init__(self) -> None:
            self.calls = 0

        def run(
            self,
            code: str,
            variables: dict[str, object] | None = None,
            *,
            on_stdout: Callable[[str], None] | None = None,
        ) -> str:
            del code, variables
            self.calls += 1
            if on_stdout is not None:
                on_stdout("output")
            return "output"

        def close(self) -> None:
            return None

    backend = _CallbackBackend()
    interpreter = DaytonaCodeInterpreter(backend=backend)

    def fail_callback(_value: str) -> None:
        raise TypeError("callback failed")

    with pytest.raises(TypeError, match="callback failed"):
        interpreter._run_backend("print('output')", None, on_stdout=fail_callback)

    assert backend.calls == 1


def test_public_output_classifies_dspy_execution_errors_before_interpreter_errors() -> None:
    from dspy import CodeExecutionError, CodeInterpreterError

    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter

    interpreter = DaytonaCodeInterpreter(backend=_FakeBackend())

    assert interpreter._public_output(CodeExecutionError("recoverable")) == "Execution error"
    assert interpreter._public_output(CodeInterpreterError("terminal")) == "Execution failed"


def test_factory_created_adapter_is_invocation_scoped_and_does_not_close_retained_backend() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    retained_backend = InProcessInterpreterBackend()
    retained = DaytonaCodeInterpreter(backend=retained_backend)

    fresh = retained.new_invocation()
    fresh.shutdown()

    assert fresh is not retained
    assert retained_backend.closed is False
    assert retained.execute("value = 1") == ""


def test_factory_created_adapter_keeps_run_bindings_off_retained_template() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    retained = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    observed: list[object] = []
    observer = observed.append

    fresh = retained.new_invocation(observer=observer, turn_request="one isolated turn")

    assert fresh._observer is observer
    assert fresh._turn_request == "one isolated turn"
    assert retained._observer is None
    assert retained._turn_request is None


def test_async_host_tool_without_bridge_fails_without_creating_loop() -> None:
    from dspy.primitives.code_interpreter import CodeInterpreterError

    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter

    async def tool():
        raise AssertionError("unbridged tool must never run")

    interpreter = DaytonaCodeInterpreter()
    interpreter._bound_tools = {"tool": tool}
    with pytest.raises(CodeInterpreterError, match="persistent async bridge"):
        interpreter.invoke_tool("tool", {})


@pytest.mark.asyncio
async def test_async_host_tool_runs_on_application_loop_through_bridge() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend, _SyncBridgeLoop

    loop = asyncio.get_running_loop()

    async def tool():
        assert asyncio.get_running_loop() is loop
        return "bridged"

    template = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend(), tools={"tool": tool})
    interpreter = template.new_invocation(async_bridge=_SyncBridgeLoop(caller_loop=loop))
    assert await asyncio.to_thread(interpreter.execute, "_out = tool()") == "bridged"
    result = await asyncio.to_thread(interpreter.execute, "SUBMIT(answer=tool())")
    assert result.output == {"answer": "bridged"}
    interpreter.shutdown()


def test_shutdown_is_idempotent() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter

    backend = _FakeBackend()
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.start()
    interp.shutdown()
    interp.shutdown()
    assert backend.closed is True


def test_strict_shutdown_preserves_broker_error_and_closes_backend() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter

    class _Broker:
        def stop(self, *, strict: bool = False) -> None:
            assert strict is True
            raise RuntimeError("broker cleanup failed")

    class _FailingCloseBackend(_FakeBackend):
        def close(self) -> None:
            self.closed = True
            raise RuntimeError("backend cleanup failed")

    backend = _FailingCloseBackend()
    interp = DaytonaCodeInterpreter(backend=backend)
    interp._http_broker = _Broker()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="broker cleanup failed"):
        interp.shutdown(strict_broker_cleanup=True)

    assert backend.closed is True


def test_lease_release_is_idempotent() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter
    from fleet_rlm.daytona.runtime import InterpreterLease

    backend = _FakeBackend()
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.start()

    lease = InterpreterLease(
        sandbox_id="sbx-1",
        interpreter_id="interp-1",
        volume_id="vol-1",
        mount_path="/home/daytona/memory",
        interpreter=interp,
    )
    lease.release()
    lease.release()
    assert backend.closed is True


def test_sanitize_provider_message_strips_secrets_and_paths() -> None:
    from fleet_rlm.daytona.errors import sanitize_provider_message

    cleaned = sanitize_provider_message("failed api_key=sk-secret path=/tmp/secret")
    assert "sk-secret" not in cleaned
    assert "/tmp/secret" not in cleaned
    assert "[redacted]" in cleaned


@pytest.mark.asyncio
async def test_sync_sandbox_bridges_async_filesystem_from_dspy_worker() -> None:
    from fleet_rlm.daytona.interpreter import sync_sandbox

    class AsyncFilesystem:
        async def download_file(self, path: str) -> bytes:
            return path.encode()

    sandbox = sync_sandbox(
        SimpleNamespace(fs=AsyncFilesystem()),
        asyncio.get_running_loop(),
    )

    assert await asyncio.to_thread(sandbox.fs.download_file, "file.txt") == (b"file.txt")


@pytest.mark.asyncio
async def test_sync_sandbox_exposes_only_explicit_async_services() -> None:
    from fleet_rlm.daytona.interpreter import sync_sandbox

    class Service:
        async def create_context(self, **kwargs):
            return kwargs

        async def run_code(self, code, **kwargs):
            return code, kwargs

        async def delete_context(self, context, **kwargs):
            return context, kwargs

        async def code_run(self, code, **kwargs):
            return code, kwargs

        async def create_session(self, session_id, **kwargs):
            return session_id, kwargs

        async def execute_session_command(self, session_id, request, **kwargs):
            return session_id, request, kwargs

        async def delete_session(self, session_id, **kwargs):
            return session_id, kwargs

        async def upload_file(self, content, path, **kwargs):
            return content, path, kwargs

        async def download_file(self, path, **kwargs):
            del kwargs
            return path.encode()

        async def delete_file(self, path, **kwargs):
            return path, kwargs

        async def list_files(self, path, **kwargs):
            return path, kwargs

    class Sandbox:
        code_interpreter = Service()
        process = Service()
        fs = Service()

        async def get_preview_link(self, port, **kwargs):
            return port, kwargs

    bridge = sync_sandbox(Sandbox(), asyncio.get_running_loop())

    def exercise() -> None:
        assert bridge.code_interpreter.create_context(language="python") == {"language": "python"}
        assert bridge.code_interpreter.run_code("1 + 1", context="ctx") == ("1 + 1", {"context": "ctx"})
        assert bridge.code_interpreter.delete_context("ctx") is None
        assert bridge.process.code_run("pwd") == ("pwd", {})
        assert bridge.process.create_session("session") == ("session", {})
        assert bridge.process.execute_session_command("session", "ls") == ("session", "ls", {})
        assert bridge.process.delete_session("session") == ("session", {})
        assert bridge.fs.upload_file(b"x", "/x") == (b"x", "/x", {})
        assert bridge.fs.download_file("/x") == b"/x"
        assert bridge.fs.delete_file("/x") == ("/x", {})
        assert bridge.fs.list_files("/") == ("/", {})
        assert bridge.get_preview_link(3000) == (3000, {})
        unknown_method = "unknown_sdk_method"
        with pytest.raises(AttributeError):
            getattr(bridge, unknown_method)

    await asyncio.to_thread(exercise)


@pytest.mark.asyncio
async def test_sync_sandbox_rejects_calls_from_owning_loop() -> None:
    from fleet_rlm.daytona.errors import DaytonaAdapterError
    from fleet_rlm.daytona.interpreter import sync_sandbox

    class Fs:
        async def download_file(self, path: str) -> bytes:
            return path.encode()

    bridge = sync_sandbox(SimpleNamespace(fs=Fs()), asyncio.get_running_loop())
    with pytest.raises(DaytonaAdapterError, match="owning event loop"):
        bridge.fs.download_file("x")


@pytest.mark.asyncio
async def test_async_volume_fs_normalizes_text_and_missing_files() -> None:
    from fleet_rlm.workspace.storage import AsyncDaytonaVolumeFS

    class Fs:
        async def download_file(self, path: str):
            if path.endswith("missing"):
                raise FileNotFoundError(path)
            return "text"

        async def delete_file(self, path: str) -> None:
            raise FileNotFoundError(path)

    volume = AsyncDaytonaVolumeFS(SimpleNamespace(fs=Fs()))
    assert await volume.read_bytes("text") == b"text"
    assert await volume.exists("text") is True
    assert await volume.exists("missing") is False
    await volume.remove("missing")


@pytest.mark.asyncio
async def test_sync_sandbox_bridges_workspace_metadata_operations() -> None:
    from fleet_rlm.daytona.interpreter import sync_sandbox

    class Fs:
        async def get_file_info(self, path: str):
            return {"path": path}

        async def create_folder(self, path: str, mode: str):
            return path, mode

    bridge = sync_sandbox(SimpleNamespace(fs=Fs()), asyncio.get_running_loop())
    assert await asyncio.to_thread(bridge.fs.get_file_info, "/workspace/a") == {"path": "/workspace/a"}
    assert await asyncio.to_thread(bridge.fs.create_folder, "/workspace/a") == ("/workspace/a", "755")
