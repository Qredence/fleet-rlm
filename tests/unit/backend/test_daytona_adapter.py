"""Daytona adapter behavior with an offline injectable backend."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


class _FakeBackend:
    """In-memory REPL stand-in for offline adapter tests."""

    def __init__(self) -> None:
        self.namespace: dict[str, object] = {"_out": ""}
        self.closed = False
        self.fail_with: BaseException | None = None

    def run(self, code: str, variables: dict[str, object] | None = None) -> str:
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
    result = interp.execute("_out = str(value)")
    assert result == "41"


def test_execute_returns_user_code_errors_for_rlm_repair() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    interp = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

    assert interp.execute("metrics = {'precision': 0.9}\nprint(metrics['prec'])") == "[Error] 'prec'"


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


def test_lease_release_is_idempotent_and_does_not_delete_sandbox() -> None:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter
    from fleet_rlm.daytona.session_manager import InterpreterLease

    backend = _FakeBackend()
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.start()
    deleted: list[str] = []

    lease = InterpreterLease(
        sandbox_id="sbx-1",
        interpreter_id="interp-1",
        volume_id="vol-1",
        mount_path="/home/daytona/memory",
        interpreter=interp,
        delete_sandbox=lambda sandbox_id: deleted.append(sandbox_id),
    )
    lease.release()
    lease.release()
    assert backend.closed is True
    assert deleted == []


def test_provider_errors_map_to_sanitized_fleet_errors() -> None:
    from daytona import DaytonaError

    from fleet_rlm.daytona.errors import DaytonaAdapterError, map_provider_error
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter

    backend = _FakeBackend()
    backend.fail_with = DaytonaError("boom api_key=sk-secret path=/tmp/secret")
    interp = DaytonaCodeInterpreter(backend=backend)
    interp.start()

    with pytest.raises(DaytonaAdapterError) as exc_info:
        interp.execute("print(1)")

    message = str(exc_info.value)
    assert "sk-secret" not in message
    assert "/tmp/secret" not in message
    mapped = map_provider_error(backend.fail_with)
    assert isinstance(mapped, DaytonaAdapterError)
    assert "sk-secret" not in mapped.message


def test_sanitize_provider_message_strips_secrets_and_paths() -> None:
    from fleet_rlm.daytona.errors import sanitize_provider_message

    cleaned = sanitize_provider_message("failed api_key=sk-secret path=/tmp/secret")
    assert "sk-secret" not in cleaned
    assert "/tmp/secret" not in cleaned
    assert "[redacted]" in cleaned


@pytest.mark.asyncio
async def test_sync_sandbox_bridges_async_filesystem_from_dspy_worker() -> None:
    from fleet_rlm.daytona.dspy_sync_bridge import sync_sandbox

    class AsyncFilesystem:
        async def download_file(self, path: str) -> bytes:
            return path.encode()

    sandbox = sync_sandbox(
        SimpleNamespace(fs=AsyncFilesystem()),
        asyncio.get_running_loop(),
    )

    assert await asyncio.to_thread(sandbox.fs.download_file, "/home/daytona/fleet/file.txt") == (
        b"/home/daytona/fleet/file.txt"
    )


@pytest.mark.asyncio
async def test_sync_sandbox_exposes_only_explicit_async_services() -> None:
    from fleet_rlm.daytona.dspy_sync_bridge import sync_sandbox

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
    from fleet_rlm.daytona.dspy_sync_bridge import sync_sandbox

    class Fs:
        async def download_file(self, path: str) -> bytes:
            return path.encode()

    bridge = sync_sandbox(SimpleNamespace(fs=Fs()), asyncio.get_running_loop())
    with pytest.raises(DaytonaAdapterError, match="owning event loop"):
        bridge.fs.download_file("/x")


@pytest.mark.asyncio
async def test_async_volume_fs_normalizes_text_and_missing_files() -> None:
    from fleet_rlm.daytona.workspace_fs import AsyncDaytonaVolumeFS

    class Fs:
        async def download_file(self, path: str):
            if path.endswith("missing"):
                raise FileNotFoundError(path)
            return "text"

        async def delete_file(self, path: str) -> None:
            raise FileNotFoundError(path)

    volume = AsyncDaytonaVolumeFS(SimpleNamespace(fs=Fs()))
    assert await volume.read_bytes("/text") == b"text"
    assert await volume.exists("/text") is True
    assert await volume.exists("/missing") is False
    await volume.remove("/missing")
