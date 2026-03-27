"""Shared helper logic for the Modal-backed runtime interpreter."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import queue
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Sequence

import modal
from dspy.primitives import CodeInterpreterError, FinalOutput

from fleet_rlm.runtime.execution.core_driver import sandbox_driver

from . import driver_factories
from .interpreter_common import execution_profile_context
from .interpreter_events import (
    complete_event_data,
    start_event_data,
)
from .interpreter_events import emit_execution_event as _emit_execution_event_impl
from .interpreter_events import summarize_code as _summarize_code_impl
from .output_utils import _redact_sensitive_text, _summarize_stdout

if TYPE_CHECKING:
    from .interpreter import ModalInterpreter
    from .profiles import ExecutionProfile

logger = logging.getLogger(__name__)


def _start_stdout_reader_impl(interpreter: ModalInterpreter) -> None:
    """Start a background thread to read sandbox stdout."""
    if interpreter._stdout_iter is None:
        return

    interpreter._stdout_queue = queue.Queue()
    q = interpreter._stdout_queue
    iterator = interpreter._stdout_iter

    def _reader() -> None:
        try:
            for line in iterator:
                q.put(line)
        except Exception:
            logger.exception("Error while reading from sandbox stdout iterator")
        finally:
            q.put(None)

    interpreter._stdout_reader_thread = threading.Thread(target=_reader, daemon=True)
    interpreter._stdout_reader_thread.start()


def _resolve_app_impl(interpreter: ModalInterpreter) -> modal.App:
    if interpreter._app_obj is not None:
        return interpreter._app_obj
    return modal.App.lookup(interpreter._app_name, create_if_missing=True)


async def _aresolve_app_impl(interpreter: ModalInterpreter) -> modal.App:
    if interpreter._app_obj is not None:
        return interpreter._app_obj
    return await modal.App.lookup.aio(interpreter._app_name, create_if_missing=True)


def _module_source_for_sandbox_impl(module: Any) -> str:
    source = inspect.getsource(module)
    return "\n".join(
        line
        for line in source.splitlines()
        if line.strip() != "from __future__ import annotations"
    )


def _build_driver_command_and_sandbox_kwargs_impl(
    interpreter: ModalInterpreter, *, app: modal.App
) -> tuple[str, dict[str, Any]]:
    with interpreter._llm_call_lock:
        interpreter._llm_call_count = 0

    from fleet_rlm.runtime.agent import session_history

    from . import sandbox_assets

    bundled_sources = [
        _module_source_for_sandbox_impl(driver_factories),
        _module_source_for_sandbox_impl(sandbox_assets),
        _module_source_for_sandbox_impl(session_history),
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


def _start_impl(interpreter: ModalInterpreter) -> None:
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


async def _astart_impl(interpreter: ModalInterpreter) -> None:
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


def _shutdown_impl(interpreter: ModalInterpreter) -> None:
    if interpreter._sandbox is not None:
        try:
            interpreter._sandbox.terminate()
        except Exception:
            logger.exception("Error while terminating Modal sandbox during shutdown")

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


async def _ashutdown_impl(interpreter: ModalInterpreter) -> None:
    if interpreter._sandbox is not None:
        try:
            if hasattr(interpreter._sandbox.terminate, "aio"):
                await interpreter._sandbox.terminate.aio()
            else:
                interpreter._sandbox.terminate()
        except Exception:
            logger.exception(
                "Error while terminating Modal sandbox during async shutdown"
            )

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


def _tool_names_impl(interpreter: ModalInterpreter) -> list[str]:
    tool_names = ["llm_query", "llm_query_batched"]
    if interpreter._tools:
        tool_names.extend(interpreter._tools.keys())
    return tool_names


def _output_names_impl(interpreter: ModalInterpreter) -> list[str]:
    if not interpreter.output_fields:
        return []
    return [
        field["name"]
        for field in interpreter.output_fields
        if isinstance(field, dict) and field.get("name")
    ]


def _summarize_stdout_impl(interpreter: ModalInterpreter, stdout: str) -> str:
    if not interpreter.summarize_stdout:
        return stdout
    return _summarize_stdout(
        stdout,
        threshold=interpreter.stdout_summary_threshold,
        prefix_len=interpreter.stdout_summary_prefix_len,
    )


def _drain_or_flush_stdin_impl(interpreter: ModalInterpreter) -> None:
    if interpreter._stdin is None:
        raise CodeInterpreterError("Sandbox input stream is not initialized")

    def _drain_or_flush() -> None:
        drain = getattr(interpreter._stdin, "drain", None)
        if callable(drain):
            drain_aio = getattr(drain, "aio", None)
            if callable(drain_aio):
                asyncio.run(drain_aio())
                return
            drain()
            return
        flush = getattr(interpreter._stdin, "flush", None)
        if callable(flush):
            flush()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _drain_or_flush()
        return

    thread_exc: Exception | None = None

    def _run_async_drain() -> None:
        nonlocal thread_exc
        try:
            _drain_or_flush()
        except Exception as exc:
            thread_exc = exc

    drain_thread = threading.Thread(
        target=_run_async_drain,
        name="modal-stdin-drain",
        daemon=True,
    )
    drain_thread.start()
    drain_thread.join()
    if thread_exc is not None:
        raise thread_exc


def _write_line_impl(interpreter: ModalInterpreter, payload: dict[str, Any]) -> None:
    if interpreter._stdin is None:
        raise CodeInterpreterError("Sandbox input stream is not initialized")
    interpreter._stdin.write(json.dumps(payload) + "\n")
    interpreter._drain_or_flush_stdin()


def _is_recoverable_exec_channel_error_impl(exc: Exception) -> bool:
    text = str(exc).lower()
    recoverable_signatures = (
        "failed to write to exec stdin",
        "broken pipe",
        "sandbox input stream is not initialized",
        "sandbox process exited unexpectedly",
    )
    return any(sig in text for sig in recoverable_signatures)


def _is_recoverable_start_error_impl(exc: Exception) -> bool:
    text = str(exc).lower()
    recoverable_signatures = (
        "timed out",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "service unavailable",
        "rate limit",
        "429",
        "resource exhausted",
    )
    return any(sig in text for sig in recoverable_signatures)


@contextmanager
def _execution_profile_impl(interpreter: ModalInterpreter, profile: ExecutionProfile):
    with execution_profile_context(interpreter, profile) as current:
        yield current


def _execute_impl(
    interpreter: ModalInterpreter,
    code: str,
    variables: dict[str, Any] | None = None,
    *,
    execution_profile: ExecutionProfile | None = None,
) -> str | FinalOutput:
    safe_vars: dict[str, Any] = {}
    for key, value in (variables or {}).items():
        try:
            json.dumps(value)
            safe_vars[key] = value
        except TypeError:
            safe_vars[key] = str(value)

    request_payload = {
        "code": code,
        "variables": safe_vars,
        "tool_names": interpreter._tool_names(),
        "output_names": interpreter._output_names(),
        "execution_profile": (
            execution_profile or interpreter.default_execution_profile
        ).value,
    }
    profile_value = str(request_payload["execution_profile"])
    code_hash, code_preview = _summarize_code_impl(code)
    started_at = time.time()
    _emit_execution_event_impl(
        interpreter,
        start_event_data(
            execution_profile=profile_value,
            code_hash=code_hash,
            code_preview=code_preview,
        ),
    )

    max_attempts = 3
    for attempt in range(max_attempts):
        if interpreter._sandbox is None:
            try:
                interpreter.start()
            except Exception as exc:
                can_retry = attempt < (max_attempts - 1) and (
                    interpreter._is_recoverable_start_error(exc)
                )
                if can_retry:
                    interpreter.shutdown()
                    time.sleep(0.25 * (2**attempt))
                    continue
                raise CodeInterpreterError(
                    f"[sandbox_unavailable] Failed to start sandbox: {exc}"
                ) from exc

        try:
            interpreter._write_line(request_payload)
            deadline = (
                time.monotonic() + interpreter.execute_timeout
                if interpreter.execute_timeout
                else None
            )

            while True:
                remaining = (
                    None if deadline is None else max(0.0, deadline - time.monotonic())
                )
                if remaining is not None and remaining <= 0:
                    interpreter.shutdown()
                    raise CodeInterpreterError(
                        f"Timed out waiting for sandbox response after {interpreter.execute_timeout}s"
                    )

                try:
                    if interpreter._stdout_queue is None:
                        raise CodeInterpreterError(
                            "Sandbox output queue is not initialized"
                        )
                    line = interpreter._stdout_queue.get(timeout=remaining)
                except queue.Empty as exc:
                    interpreter.shutdown()
                    raise CodeInterpreterError(
                        f"Timed out waiting for sandbox response after {interpreter.execute_timeout}s"
                    ) from exc

                if line is None:
                    stderr_tail = ""
                    try:
                        if interpreter._stderr_iter is not None:
                            stderr_tail = "".join(list(interpreter._stderr_iter)[:50])
                    except Exception:
                        stderr_tail = ""
                    msg = "Modal sandbox process exited unexpectedly."
                    if stderr_tail:
                        msg += f"\nStderr: {_redact_sensitive_text(stderr_tail)}"
                    raise CodeInterpreterError(msg)

                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if "tool_call" in message:
                    call = message["tool_call"] or {}
                    name = call.get("name")
                    args = call.get("args") or []
                    kwargs = call.get("kwargs") or {}
                    try:
                        if name == "llm_query":
                            result = interpreter.llm_query(*args, **kwargs)
                        elif name == "llm_query_batched":
                            result = interpreter.llm_query_batched(*args, **kwargs)
                        elif name and name in interpreter._tools:
                            result = interpreter._tools[name](*args, **kwargs)
                        else:
                            raise CodeInterpreterError(f"Unknown tool: {name}")

                        try:
                            json.dumps(result)
                            reply = {"tool_result": result}
                        except TypeError:
                            reply = {"tool_result": str(result)}
                    except Exception as exc:
                        reply = {"tool_error": f"{type(exc).__name__}: {exc}"}

                    interpreter._write_line(reply)
                    continue

                if "stdout" in message or "stderr" in message or "final" in message:
                    stdout = message.get("stdout", "") or ""
                    stderr = message.get("stderr", "") or ""
                    final_obj = message.get("final")

                    if final_obj is not None:
                        output_keys = (
                            list(final_obj.keys())[:50]
                            if isinstance(final_obj, dict)
                            else None
                        )
                        _emit_execution_event_impl(
                            interpreter,
                            complete_event_data(
                                started_at=started_at,
                                execution_profile=profile_value,
                                code_hash=code_hash,
                                code_preview=code_preview,
                                success=True,
                                result_kind="final_output",
                                output_keys=output_keys,
                            ),
                        )
                        return FinalOutput(final_obj)

                    if stderr:
                        summarized_stdout = interpreter._summarize_stdout(stdout)
                        _emit_execution_event_impl(
                            interpreter,
                            complete_event_data(
                                started_at=started_at,
                                execution_profile=profile_value,
                                code_hash=code_hash,
                                code_preview=code_preview,
                                success=False,
                                result_kind="stderr",
                                stderr_preview=_redact_sensitive_text(stderr),
                            ),
                        )
                        return (
                            summarized_stdout
                            + ("\n" if summarized_stdout else "")
                            + stderr
                        )

                    stdout_preview = interpreter._summarize_stdout(stdout)
                    _emit_execution_event_impl(
                        interpreter,
                        complete_event_data(
                            started_at=started_at,
                            execution_profile=profile_value,
                            code_hash=code_hash,
                            code_preview=code_preview,
                            success=True,
                            result_kind="stdout",
                            stdout_preview=stdout_preview,
                        ),
                    )
                    return stdout_preview
        except Exception as exc:
            can_retry = attempt < (max_attempts - 1) and (
                interpreter._is_recoverable_exec_channel_error(exc)
            )
            if can_retry:
                interpreter.shutdown()
                time.sleep(0.25 * (2**attempt))
                continue
            _emit_execution_event_impl(
                interpreter,
                complete_event_data(
                    started_at=started_at,
                    execution_profile=profile_value,
                    code_hash=code_hash,
                    code_preview=code_preview,
                    success=False,
                    result_kind="exception",
                    error_type=type(exc).__name__,
                    error=str(exc),
                ),
            )
            raise

    _emit_execution_event_impl(
        interpreter,
        complete_event_data(
            started_at=started_at,
            execution_profile=profile_value,
            code_hash=code_hash,
            code_preview=code_preview,
            success=False,
            result_kind="retry_exhausted",
        ),
    )
    raise CodeInterpreterError(
        "[sandbox_unavailable] Unexpected execute retry exhaustion"
    )


def _build_default_image(
    *, python_version: str, pip_packages: Sequence[str]
) -> modal.Image:
    """Build a default Modal image for sandbox execution."""
    return modal.Image.debian_slim(python_version=python_version).pip_install(
        *pip_packages
    )
