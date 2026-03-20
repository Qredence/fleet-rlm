"""Protocol/session helpers for :mod:`fleet_rlm.core.execution.interpreter`."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from dspy.primitives import CodeInterpreterError, FinalOutput

from fleet_rlm.core.tools.output_utils import (
    _redact_sensitive_text,
    _summarize_stdout as _summarize_stdout_util,
)

from .interpreter_events import (
    complete_event_data,
    emit_execution_event,
    start_event_data,
    summarize_code,
)

if TYPE_CHECKING:
    from .interpreter import ModalInterpreter
    from .profiles import ExecutionProfile


def tool_names(interpreter: "ModalInterpreter") -> list[str]:
    """Get the list of registered tool names."""
    tools = ["llm_query", "llm_query_batched"]
    if interpreter._tools:
        tools.extend(interpreter._tools.keys())
    return tools


def output_names(interpreter: "ModalInterpreter") -> list[str]:
    """Get the list of output field names."""
    if not interpreter.output_fields:
        return []
    return [
        field["name"]
        for field in interpreter.output_fields
        if isinstance(field, dict) and field.get("name")
    ]


def summarize_stdout(interpreter: "ModalInterpreter", stdout: str) -> str:
    """Summarize stdout output to prevent context window pollution."""
    if not interpreter.summarize_stdout:
        return stdout
    return _summarize_stdout_util(
        stdout,
        threshold=interpreter.stdout_summary_threshold,
        prefix_len=interpreter.stdout_summary_prefix_len,
    )


def drain_or_flush_stdin(interpreter: "ModalInterpreter") -> None:
    """Flush sandbox stdin, preferring Modal's async drain when available."""
    if interpreter._stdin is None:
        raise CodeInterpreterError("Sandbox input stream is not initialized")

    def _drain_or_flush_impl() -> None:
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
        _drain_or_flush_impl()
        return

    thread_exc: Exception | None = None

    def _run_async_drain() -> None:
        nonlocal thread_exc
        try:
            _drain_or_flush_impl()
        except Exception as exc:  # pragma: no cover - exercised via caller
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


def write_line(interpreter: "ModalInterpreter", payload: dict[str, Any]) -> None:
    """Write a JSON payload to the sandbox stdin."""
    if interpreter._stdin is None:
        raise CodeInterpreterError("Sandbox input stream is not initialized")
    interpreter._stdin.write(json.dumps(payload) + "\n")
    interpreter._drain_or_flush_stdin()


def is_recoverable_exec_channel_error(exc: Exception) -> bool:
    """Return ``True`` when an exec transport error is likely transient."""
    text = str(exc).lower()
    recoverable_signatures = (
        "failed to write to exec stdin",
        "broken pipe",
        "sandbox input stream is not initialized",
        "sandbox process exited unexpectedly",
    )
    return any(sig in text for sig in recoverable_signatures)


def is_recoverable_start_error(exc: Exception) -> bool:
    """Return ``True`` when sandbox startup failures are likely transient."""
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
def execution_profile(interpreter: "ModalInterpreter", profile: "ExecutionProfile"):
    """Temporarily override the default execution profile."""
    previous = interpreter.default_execution_profile
    interpreter.default_execution_profile = profile
    try:
        yield interpreter
    finally:
        interpreter.default_execution_profile = previous


def execute(
    interpreter: "ModalInterpreter",
    code: str,
    variables: dict[str, Any] | None = None,
    *,
    execution_profile: "ExecutionProfile" | None = None,
) -> str | FinalOutput:
    """Execute Python code in the Modal sandbox."""
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
    code_hash, code_preview = summarize_code(code)
    started_at = time.time()
    emit_execution_event(
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
                        emit_execution_event(
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
                        emit_execution_event(
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
                    emit_execution_event(
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
            emit_execution_event(
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

    emit_execution_event(
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
