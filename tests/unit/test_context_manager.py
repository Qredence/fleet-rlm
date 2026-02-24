"""Unit tests for ModalInterpreter context manager support.

These tests mock Modal to avoid requiring credentials. They verify
the ``__enter__`` / ``__exit__`` protocol is wired correctly.

Run with: uv run pytest tests/test_context_manager.py -v
"""

from __future__ import annotations

import json
import queue
from unittest.mock import MagicMock, patch

import pytest


class _FakeSandbox:
    """Mock Modal Sandbox."""

    def __init__(self, **kwargs):
        self._terminated = False

    def exec(self, *args, **kwargs):
        proc = MagicMock()
        proc.stdin = MagicMock()
        # Return an iterator that immediately signals EOF
        proc.stdout = iter([])
        proc.stderr = iter([])
        return proc

    def terminate(self):
        self._terminated = True


class _FakeApp:
    """Mock Modal App."""

    pass


class _FakeSecret:
    """Mock Modal Secret."""

    @classmethod
    def from_name(cls, name):
        return cls()


class _FakeImage:
    """Mock Modal Image."""

    @staticmethod
    def debian_slim(**kwargs):
        return _FakeImage()

    def pip_install(self, *args):
        return self


@pytest.fixture
def mock_modal(monkeypatch):
    """Patch Modal classes for unit testing."""
    import modal

    monkeypatch.setattr(modal, "App", MagicMock(return_value=_FakeApp()))
    monkeypatch.setattr(modal.App, "lookup", MagicMock(return_value=_FakeApp()))
    monkeypatch.setattr(
        modal, "Sandbox", MagicMock(create=MagicMock(return_value=_FakeSandbox()))
    )
    monkeypatch.setattr(modal.Sandbox, "create", MagicMock(return_value=_FakeSandbox()))
    monkeypatch.setattr(modal, "Secret", _FakeSecret)
    monkeypatch.setattr(modal, "Image", _FakeImage)


class TestContextManager:
    """Test ModalInterpreter as a context manager."""

    def test_enter_calls_start(self, mock_modal):
        """__enter__ should call start() and return self."""
        from fleet_rlm.core.interpreter import ModalInterpreter

        interp = ModalInterpreter(timeout=10)

        with (
            patch.object(interp, "start") as mock_start,
            patch.object(interp, "shutdown"),
        ):
            result = interp.__enter__()

        assert result is interp
        mock_start.assert_called_once()

    def test_exit_calls_shutdown(self, mock_modal):
        """__exit__ should call shutdown()."""
        from fleet_rlm.core.interpreter import ModalInterpreter

        interp = ModalInterpreter(timeout=10)

        with patch.object(interp, "shutdown") as mock_shutdown:
            interp.__exit__(None, None, None)

        mock_shutdown.assert_called_once()

    def test_exit_returns_false(self, mock_modal):
        """__exit__ should return False (don't suppress exceptions)."""
        from fleet_rlm.core.interpreter import ModalInterpreter

        interp = ModalInterpreter(timeout=10)

        with patch.object(interp, "shutdown"):
            result = interp.__exit__(ValueError, ValueError("test"), None)

        assert result is False

    def test_exit_called_on_exception(self, mock_modal):
        """__exit__ should be called even when exception occurs."""
        from fleet_rlm.core.interpreter import ModalInterpreter

        interp = ModalInterpreter(timeout=10)

        with (
            patch.object(interp, "start"),
            patch.object(interp, "shutdown") as mock_shutdown,
        ):
            with pytest.raises(ValueError, match="test error"):
                with interp:
                    raise ValueError("test error")

            mock_shutdown.assert_called_once()

    def test_context_manager_full_lifecycle(self, mock_modal):
        """Test the full with-block lifecycle."""
        from fleet_rlm.core.interpreter import ModalInterpreter

        interp = ModalInterpreter(timeout=10)

        start_called = False
        shutdown_called = False

        def mock_start():
            nonlocal start_called
            start_called = True

        def mock_shutdown():
            nonlocal shutdown_called
            shutdown_called = True

        with (
            patch.object(interp, "start", mock_start),
            patch.object(interp, "shutdown", mock_shutdown),
        ):
            with interp as ctx:
                assert ctx is interp
                assert start_called
                assert not shutdown_called

        assert shutdown_called


@pytest.mark.asyncio
async def test_aexecute_uses_to_thread_when_enabled(mock_modal, monkeypatch):
    """aexecute() should dispatch execute() via asyncio.to_thread by default."""
    from fleet_rlm.core.interpreter import ModalInterpreter

    interp = ModalInterpreter(timeout=10, async_execute=True)
    calls: dict[str, object] = {}

    def fake_execute(code, variables=None, *, execution_profile=None):
        calls["execute"] = {
            "code": code,
            "variables": variables,
            "execution_profile": execution_profile,
        }
        return "ok"

    async def fake_to_thread(func, *args, **kwargs):
        calls["to_thread"] = True
        return func(*args, **kwargs)

    monkeypatch.setattr(interp, "execute", fake_execute)
    monkeypatch.setattr("fleet_rlm.core.interpreter.asyncio.to_thread", fake_to_thread)

    result = await interp.aexecute("print('hi')", {"x": 1})
    assert result == "ok"
    assert calls.get("to_thread") is True
    assert calls["execute"]["code"] == "print('hi')"


@pytest.mark.asyncio
async def test_aexecute_runs_sync_directly_when_disabled(mock_modal, monkeypatch):
    """aexecute() should call execute() directly when async_execute is disabled."""
    from fleet_rlm.core.interpreter import ModalInterpreter

    interp = ModalInterpreter(timeout=10, async_execute=False)
    calls: dict[str, object] = {}

    def fake_execute(code, variables=None, *, execution_profile=None):
        calls["execute"] = code
        return "sync-ok"

    async def fail_to_thread(*args, **kwargs):
        raise AssertionError(
            "asyncio.to_thread should not be used when async_execute=False"
        )

    monkeypatch.setattr(interp, "execute", fake_execute)
    monkeypatch.setattr("fleet_rlm.core.interpreter.asyncio.to_thread", fail_to_thread)

    result = await interp.aexecute("x = 1")
    assert result == "sync-ok"
    assert calls["execute"] == "x = 1"


@pytest.mark.asyncio
async def test_async_context_manager_calls_start_and_shutdown(mock_modal):
    """__aenter__/__aexit__ should mirror sync lifecycle semantics."""
    from fleet_rlm.core.interpreter import ModalInterpreter

    interp = ModalInterpreter(timeout=10, async_execute=False)

    with (
        patch.object(interp, "start") as mock_start,
        patch.object(interp, "shutdown") as mock_shutdown,
    ):
        async with interp as ctx:
            assert ctx is interp
            mock_start.assert_called_once()
            mock_shutdown.assert_not_called()

    mock_shutdown.assert_called_once()


def test_write_line_prefers_drain_aio_when_available(mock_modal, monkeypatch):
    """_write_line should use drain.aio() when Modal-style async drain is present."""
    from fleet_rlm.core.interpreter import ModalInterpreter

    interp = ModalInterpreter(timeout=10)
    calls: dict[str, object] = {"writes": []}

    class _DrainWithAio:
        def __call__(self):
            calls["sync_drain_called"] = True

        async def aio(self):
            calls["aio_drain_called"] = True

    class _Writer:
        def __init__(self):
            self.drain = _DrainWithAio()

        def write(self, data):
            calls["writes"].append(data)

    interp._stdin = _Writer()

    def _raise_no_running_loop():
        raise RuntimeError("no running loop")

    monkeypatch.setattr(
        "fleet_rlm.core.interpreter.asyncio.get_running_loop",
        _raise_no_running_loop,
    )

    interp._write_line({"hello": "world"})

    assert calls.get("aio_drain_called") is True
    assert calls.get("sync_drain_called") is not True
    assert calls["writes"] == ['{"hello": "world"}\n']


def test_write_line_falls_back_to_sync_drain_when_aio_missing(mock_modal):
    """_write_line should use blocking drain() when drain.aio() is unavailable."""
    from fleet_rlm.core.interpreter import ModalInterpreter

    interp = ModalInterpreter(timeout=10)
    calls: dict[str, object] = {"writes": []}

    class _Writer:
        def write(self, data):
            calls["writes"].append(data)

        def drain(self):
            calls["sync_drain_called"] = True

    interp._stdin = _Writer()
    interp._write_line({"ok": True})

    assert calls.get("sync_drain_called") is True
    assert calls["writes"] == ['{"ok": true}\n']


def test_write_line_falls_back_to_flush_when_drain_missing(mock_modal):
    """_write_line should use flush() when no drain() method exists."""
    from fleet_rlm.core.interpreter import ModalInterpreter

    interp = ModalInterpreter(timeout=10)
    calls: dict[str, object] = {"writes": []}

    class _Writer:
        def write(self, data):
            calls["writes"].append(data)

        def flush(self):
            calls["flush_called"] = True

    interp._stdin = _Writer()
    interp._write_line({"n": 1})

    assert calls.get("flush_called") is True
    assert calls["writes"] == ['{"n": 1}\n']


def test_write_line_raises_when_stdin_missing(mock_modal):
    """_write_line should error clearly when sandbox stdin is unavailable."""
    from dspy.primitives.code_interpreter import CodeInterpreterError

    from fleet_rlm.core.interpreter import ModalInterpreter

    interp = ModalInterpreter(timeout=10)
    interp._stdin = None

    with pytest.raises(
        CodeInterpreterError, match="Sandbox input stream is not initialized"
    ):
        interp._write_line({"x": 1})


def test_write_line_does_not_call_asyncio_run_when_event_loop_present(
    mock_modal, monkeypatch
):
    """_write_line should still use drain.aio() via helper thread when loop is running."""
    from fleet_rlm.core.interpreter import ModalInterpreter

    interp = ModalInterpreter(timeout=10)
    calls: dict[str, object] = {"writes": []}

    class _DrainWithAio:
        def __call__(self):
            calls["sync_drain_called"] = True

        async def aio(self):
            calls["aio_drain_called"] = True

    class _Writer:
        def __init__(self):
            self.drain = _DrainWithAio()

        def write(self, data):
            calls["writes"].append(data)

    interp._stdin = _Writer()
    monkeypatch.setattr(
        "fleet_rlm.core.interpreter.asyncio.get_running_loop",
        object,
    )

    interp._write_line({"mode": "fallback"})

    assert calls.get("sync_drain_called") is not True
    assert calls.get("aio_drain_called") is True


def test_start_bundles_driver_dependencies_in_exec_command(mock_modal, monkeypatch):
    """start() should embed helper module sources so sandbox needn't import fleet_rlm."""
    from fleet_rlm.core.interpreter import ModalInterpreter
    import modal

    captured: dict[str, object] = {}

    class _CaptureSandbox(_FakeSandbox):
        def exec(self, *args, **kwargs):
            captured["args"] = args
            return super().exec(*args, **kwargs)

    monkeypatch.setattr(
        modal.Sandbox, "create", MagicMock(return_value=_CaptureSandbox())
    )

    interp = ModalInterpreter(timeout=10)
    interp.start()
    try:
        cmd = str((captured.get("args") or ("", "", "", ""))[3])
        assert "def make_send(" in cmd
        assert "def peek(" in cmd
        assert "def reset_session_history(" in cmd
        assert "def save_to_volume(" in cmd
        assert "def sandbox_driver(" in cmd
        assert "sandbox_driver()" in cmd
    finally:
        interp.shutdown()


def test_execute_retries_once_on_modal_exec_stdin_failure(mock_modal, monkeypatch):
    """execute() should restart sandbox and retry once on transient stdin channel errors."""
    from fleet_rlm.core.interpreter import ModalInterpreter

    interp = ModalInterpreter(timeout=10)
    starts = {"count": 0}
    writes = {"count": 0}
    shutdowns = {"count": 0}

    def fake_start():
        starts["count"] += 1
        interp._sandbox = object()
        interp._stderr_iter = iter([])
        interp._stdout_queue = queue.Queue()
        if starts["count"] == 2:
            interp._stdout_queue.put(json.dumps({"final": {"status": "ok"}}))

    def fake_shutdown():
        shutdowns["count"] += 1
        interp._sandbox = None
        interp._stdin = None
        interp._stdout_queue = None
        interp._stderr_iter = None

    def fake_write_line(_payload):
        writes["count"] += 1
        if writes["count"] == 1:
            raise RuntimeError(
                "Failed to write to exec stdin: please contact support@modal.com"
            )

    monkeypatch.setattr(interp, "start", fake_start)
    monkeypatch.setattr(interp, "shutdown", fake_shutdown)
    monkeypatch.setattr(interp, "_write_line", fake_write_line)

    result = interp.execute("print('hi')")
    assert result.output["status"] == "ok"
    assert starts["count"] == 2
    assert shutdowns["count"] == 1
    assert writes["count"] == 2


def test_execute_emits_repl_hook_start_and_complete(mock_modal, monkeypatch):
    """execute() should emit start/complete hook payloads for REPL observability."""
    from dspy.primitives.code_interpreter import FinalOutput

    from fleet_rlm.core.interpreter import ModalInterpreter

    interp = ModalInterpreter(timeout=10)
    hook_events: list[dict[str, object]] = []

    def fake_start():
        interp._sandbox = object()
        interp._stderr_iter = iter([])
        interp._stdout_queue = queue.Queue()
        interp._stdout_queue.put(json.dumps({"final": {"status": "ok"}}))

    monkeypatch.setattr(interp, "start", fake_start)
    monkeypatch.setattr(interp, "_write_line", lambda payload: None)
    interp.execution_event_callback = hook_events.append

    result = interp.execute("print('hi')")
    assert isinstance(result, FinalOutput)
    assert hook_events
    assert hook_events[0]["phase"] == "start"
    assert hook_events[-1]["phase"] == "complete"
    assert hook_events[-1]["success"] is True
    assert hook_events[-1]["result_kind"] == "final_output"


def test_execute_does_not_retry_non_channel_errors(mock_modal, monkeypatch):
    """execute() should surface non-transport failures immediately."""
    from fleet_rlm.core.interpreter import ModalInterpreter

    interp = ModalInterpreter(timeout=10)
    starts = {"count": 0}
    writes = {"count": 0}
    shutdowns = {"count": 0}

    def fake_start():
        starts["count"] += 1
        interp._sandbox = object()
        interp._stderr_iter = iter([])
        interp._stdout_queue = queue.Queue()

    def fake_shutdown():
        shutdowns["count"] += 1
        interp._sandbox = None

    def fake_write_line(_payload):
        writes["count"] += 1
        raise RuntimeError("Permission denied")

    monkeypatch.setattr(interp, "start", fake_start)
    monkeypatch.setattr(interp, "shutdown", fake_shutdown)
    monkeypatch.setattr(interp, "_write_line", fake_write_line)

    with pytest.raises(RuntimeError, match="Permission denied"):
        interp.execute("print('hi')")

    assert starts["count"] == 1
    assert shutdowns["count"] == 0
    assert writes["count"] == 1
