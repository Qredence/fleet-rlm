"""Lifecycle helpers for :mod:`fleet_rlm.core.execution.interpreter`."""

from __future__ import annotations

import inspect
import queue
import threading
from typing import TYPE_CHECKING, Any

import modal

from fleet_rlm.core.execution.core_driver import sandbox_driver

from . import driver_factories

if TYPE_CHECKING:
    from .interpreter import ModalInterpreter


def start_stdout_reader(interpreter: "ModalInterpreter") -> None:
    """Start a background thread to read sandbox stdout."""
    if interpreter._stdout_iter is None:
        return

    interpreter._stdout_queue = queue.Queue()
    q = interpreter._stdout_queue
    it = interpreter._stdout_iter

    def _reader() -> None:
        try:
            for line in it:
                q.put(line)
        except Exception:
            pass
        finally:
            q.put(None)

    interpreter._stdout_reader_thread = threading.Thread(target=_reader, daemon=True)
    interpreter._stdout_reader_thread.start()


def resolve_app(interpreter: "ModalInterpreter") -> modal.App:
    """Return a fresh App handle."""
    if interpreter._app_obj is not None:
        return interpreter._app_obj
    return modal.App.lookup(interpreter._app_name, create_if_missing=True)


async def aresolve_app(interpreter: "ModalInterpreter") -> modal.App:
    """Return a fresh App handle (async)."""
    if interpreter._app_obj is not None:
        return interpreter._app_obj
    return await modal.App.lookup.aio(interpreter._app_name, create_if_missing=True)


def module_source_for_sandbox(module: Any) -> str:
    """Return module source with future-import lines stripped for embedding."""
    source = inspect.getsource(module)
    return "\n".join(
        line
        for line in source.splitlines()
        if line.strip() != "from __future__ import annotations"
    )


def build_driver_command_and_sandbox_kwargs(
    interpreter: "ModalInterpreter", *, app: modal.App
) -> tuple[str, dict[str, Any]]:
    """Build sandbox driver command and kwargs shared by start/astart."""
    with interpreter._llm_call_lock:
        interpreter._llm_call_count = 0

    # Deferred imports to break circular dependency:
    # interpreter → core.tools → interpreter (for ExecutionProfile)
    from fleet_rlm.core.agent import session_history
    from fleet_rlm.core.tools import sandbox_tools
    from fleet_rlm.core.tools import volume_tools

    bundled_sources = [
        module_source_for_sandbox(driver_factories),
        module_source_for_sandbox(sandbox_tools),
        module_source_for_sandbox(session_history),
        module_source_for_sandbox(volume_tools),
        inspect.getsource(sandbox_driver),
        "sandbox_driver()",
    ]
    driver_command = "\n\n".join(bundled_sources)

    sandbox_kwargs: dict[str, Any] = {
        "app": app,
        "image": interpreter.image,
        "secrets": interpreter.secrets,
        "timeout": interpreter.timeout,
    }
    if interpreter.idle_timeout is not None:
        sandbox_kwargs["idle_timeout"] = interpreter.idle_timeout
    if interpreter.volume_name:
        interpreter._volume = interpreter._resolve_volume()
        sandbox_kwargs["volumes"] = {interpreter.volume_mount_path: interpreter._volume}

    return driver_command, sandbox_kwargs


def start(interpreter: "ModalInterpreter") -> None:
    """Start the Modal sandbox and initialize the driver process."""
    if interpreter._sandbox is not None:
        return

    app = interpreter._resolve_app()
    driver_command, sandbox_kwargs = (
        interpreter._build_driver_command_and_sandbox_kwargs(app=app)
    )

    interpreter._sandbox = modal.Sandbox.create(**sandbox_kwargs)
    interpreter._proc = interpreter._sandbox.exec(
        "python", "-u", "-c", driver_command, bufsize=1, timeout=interpreter.timeout
    )

    interpreter._stdin = interpreter._proc.stdin
    interpreter._stdout_iter = iter(interpreter._proc.stdout)
    interpreter._stderr_iter = iter(getattr(interpreter._proc, "stderr", []))
    interpreter._start_stdout_reader()


async def astart(interpreter: "ModalInterpreter") -> None:
    """Start the Modal sandbox and initialize the driver process (async)."""
    if interpreter._sandbox is not None:
        return

    app = await interpreter._aresolve_app()
    driver_command, sandbox_kwargs = (
        interpreter._build_driver_command_and_sandbox_kwargs(app=app)
    )

    interpreter._sandbox = await modal.Sandbox.create.aio(**sandbox_kwargs)
    interpreter._proc = await interpreter._sandbox.exec.aio(
        "python", "-u", "-c", driver_command, bufsize=1, timeout=interpreter.timeout
    )

    interpreter._stdin = interpreter._proc.stdin
    interpreter._stdout_iter = iter(interpreter._proc.stdout)
    interpreter._stderr_iter = iter(getattr(interpreter._proc, "stderr", []))
    interpreter._start_stdout_reader()


def shutdown(interpreter: "ModalInterpreter") -> None:
    """Terminate the sandbox and clean up all resources."""
    if interpreter._sandbox is not None:
        try:
            interpreter._sandbox.terminate()
        except Exception:
            pass

    interpreter._sandbox = None
    interpreter._proc = None
    interpreter._stdin = None
    interpreter._stdout_iter = None
    interpreter._stdout_queue = None
    interpreter._stdout_reader_thread = None
    interpreter._stderr_iter = None
    interpreter._volume = None
    with interpreter._sub_lm_executor_lock:
        if interpreter._sub_lm_executor is not None:
            interpreter._sub_lm_executor.shutdown(wait=False, cancel_futures=True)
            interpreter._sub_lm_executor = None


async def ashutdown(interpreter: "ModalInterpreter") -> None:
    """Terminate the sandbox and clean up all resources (async)."""
    if interpreter._sandbox is not None:
        try:
            if hasattr(interpreter._sandbox.terminate, "aio"):
                await interpreter._sandbox.terminate.aio()
            else:
                interpreter._sandbox.terminate()
        except Exception:
            pass

    if interpreter._stdout_reader_thread is not None:
        interpreter._stdout_reader_thread.join(timeout=2.0)
        interpreter._stdout_reader_thread = None

    interpreter._sandbox = None
    interpreter._proc = None
    interpreter._stdin = None
    interpreter._stdout_iter = None
    interpreter._stdout_queue = None
    interpreter._stderr_iter = None
    interpreter._volume = None
    with interpreter._sub_lm_executor_lock:
        if interpreter._sub_lm_executor is not None:
            interpreter._sub_lm_executor.shutdown(wait=False, cancel_futures=True)
            interpreter._sub_lm_executor = None
