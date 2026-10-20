"""Host side of the Daytona REPL transport: Daytona interpreter contexts and the stdout bridge protocol.

Each Fleet namespace is one ``code_interpreter`` context, prepared once with the Skill and workspace
helpers plus ``runtime/repl.py``. An execution is one ``run_code`` call. The sandbox reports host-tool
calls and the final result as marker lines on stdout (see ``runtime/repl.py``); host calls run on their
own threads so the websocket keeps reading, and replies go back through ``fs.upload_file``.
"""

from __future__ import annotations

import contextvars
import json
import logging
import random
import threading
import time
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any, TypeVar

from daytona.common.errors import DaytonaConnectionError, DaytonaError, DaytonaRateLimitError, DaytonaTimeoutError

_LOGGER = logging.getLogger(__name__)
CALL_MARK = "\x1eFLEET_CALL "
RESULT_MARK = "\x1eFLEET_RESULT "
REPLY_DIR = "/tmp/fleet/replies"
_PREAMBLE_TIMEOUT_SECONDS = 60
# A host tool that never answers (lost reply upload, hung tool) fails the sandbox call after this long
# instead of stalling the execution until the Turn watchdog stops the sandbox.
HOST_CALL_TIMEOUT_SECONDS = 600.0
# While `run_code` blocks, the sandbox's provider state is polled this often; a lost sandbox fails the
# execution promptly instead of leaving the websocket waiting for the execution timeout.
LIVENESS_POLL_SECONDS = 5.0
_RETRY_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 0.5

_T = TypeVar("_T")


def is_transient(exc: BaseException) -> bool:
    """Daytona transport, timeout, rate-limit, and 5xx failures; everything else is a real answer."""
    if isinstance(exc, (DaytonaConnectionError, DaytonaTimeoutError, DaytonaRateLimitError)):
        return True
    status = exc.status_code if isinstance(exc, DaytonaError) else None
    return isinstance(status, int) and status >= 500


def with_retries(operation: Callable[[], _T], *, before_retry: Callable[[], None] | None = None) -> _T:
    """Run an idempotent Daytona call, retrying transient failures with jittered exponential backoff.

    Never wrap `run_code`: a timed-out or disconnected execution may have run, and must not be replayed.
    """
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return operation()
        except Exception as exc:
            if attempt == _RETRY_ATTEMPTS - 1 or not is_transient(exc):
                raise
            _LOGGER.warning("Transient Daytona error (%s); retrying", type(exc).__name__)
            time.sleep(_RETRY_BASE_SECONDS * (2**attempt) * (0.5 + random.random()))
            if before_retry is not None:
                before_retry()
    raise AssertionError("unreachable")


class ExecutionOutcomeUnknownError(RuntimeError):
    """Remote work may have executed; the Turn must not replay it."""

    transport_retries: int = 0


class SandboxLostError(ExecutionOutcomeUnknownError):
    """The sandbox was stopped, destroyed, or deleted while an execution was running in it."""


@cache
def preamble() -> str:
    """Sandbox code that prepares a fresh interpreter context."""
    from fleet_rlm.skills import SANDBOX_SKILL_HELPERS_CODE
    from fleet_rlm.workspace import WORKSPACE_CODE

    runtime = (Path(__file__).parent / "runtime" / "repl.py").read_text(encoding="utf-8")
    return "\n".join([SANDBOX_SKILL_HELPERS_CODE, WORKSPACE_CODE, runtime])


def create_context(sandbox: Any) -> Any:
    """Create a sandbox interpreter context and prepare it with the runtime preamble."""
    interpreter = sandbox.code_interpreter
    context = interpreter.create_context()
    prepared = interpreter.run_code(preamble(), context=context, timeout=_PREAMBLE_TIMEOUT_SECONDS)
    if prepared.error is not None:
        _delete_quietly(sandbox, context)
        raise RuntimeError(f"Sandbox interpreter preparation failed ({prepared.error.name})")
    return context


def delete_context(sandbox: Any, context: Any) -> None:
    """Delete a sandbox interpreter context, ignoring cleanup failures."""
    _delete_quietly(sandbox, context)


def _delete_quietly(sandbox: Any, context: Any) -> None:
    try:
        sandbox.code_interpreter.delete_context(context)
    except Exception:
        _LOGGER.debug("Sandbox interpreter context cleanup did not complete")


class _Protocol:
    """Reassemble marker lines from streamed stdout chunks; dispatch host calls; keep the result."""

    def __init__(self, sandbox: Any, on_call: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.sandbox = sandbox
        self.on_call = on_call
        self.result: dict[str, Any] | None = None
        self._buffer = ""
        self._workers: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._finished = False

    def feed(self, message: Any) -> None:
        with self._lock:
            # An abandoned run_code thread may still deliver output after the execution was given up.
            if self._finished:
                return
            self._buffer += message.output
            *lines, self._buffer = self._buffer.split("\n")
            for line in lines:
                self._line(line)

    def finish(self) -> None:
        with self._lock:
            self._finished = True
            if self._buffer:
                self._line(self._buffer)
                self._buffer = ""
            workers = tuple(self._workers)
        for worker in workers:
            worker.join(timeout=5)

    def _line(self, line: str) -> None:
        if line.startswith(RESULT_MARK):
            self.result = json.loads(line[len(RESULT_MARK) :])
        elif line.startswith(CALL_MARK):
            call = json.loads(line[len(CALL_MARK) :])
            worker = threading.Thread(target=self._answer, args=(call,), name="fleet-host-call", daemon=True)
            self._workers.append(worker)
            worker.start()

    def _answer(self, call: dict[str, Any]) -> None:
        reply = self.on_call(call)
        try:
            body = json.dumps(reply, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            body = json.dumps({"error": "Host tool result is not JSON-serializable"}).encode("utf-8")
        try:
            with_retries(lambda: self.sandbox.fs.upload_file(body, f"{REPLY_DIR}/{call['id']}.json"))
        except Exception:
            # The sandbox call times out (HOST_CALL_TIMEOUT_SECONDS) and the model sees the error.
            _LOGGER.warning("Could not return a host tool result to the sandbox")


def run(
    sandbox: Any,
    context: Any,
    payload: dict[str, Any],
    *,
    on_call: Callable[[dict[str, Any]], dict[str, Any]],
    timeout: int,
    alive: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run one execution in `context`; return the runtime's result dict.

    Raises the SDK's error when the transport fails; the caller decides whether the outcome is known.
    With `alive`, the sandbox is polled while the execution runs and `SandboxLostError` is raised as
    soon as it reports False, rather than waiting for the websocket or the execution timeout.
    """
    protocol = _Protocol(sandbox, on_call)
    payload = {"host_call_timeout": min(HOST_CALL_TIMEOUT_SECONDS, timeout), **payload}
    code = f"_fleet_exec({json.dumps(payload, separators=(',', ':'))!r})"

    def execute() -> Any:
        return sandbox.code_interpreter.run_code(code, context=context, on_stdout=protocol.feed, timeout=timeout)

    try:
        execution = execute() if alive is None else _run_watched(execute, alive)
    finally:
        protocol.finish()
    if protocol.result is not None:
        return protocol.result
    if execution.error is not None:
        # The runtime catches user errors itself; this is the context failing around it.
        return {
            "stdout": "",
            "stderr": (execution.error.traceback or execution.error.value or "")[-20_000:],
            "error": True,
            "error_type": execution.error.name or "PythonError",
        }
    raise ExecutionOutcomeUnknownError("The sandbox interpreter returned no result")


def _run_watched(execute: Callable[[], _T], alive: Callable[[], bool]) -> _T:
    """Run a blocking `run_code` on a worker thread, polling `alive` until it returns or the sandbox is lost.

    A lost execution's thread is abandoned, never retried; it ends when the SDK call fails or times out.
    """
    outcome: dict[str, Any] = {}
    done = threading.Event()

    def target() -> None:
        try:
            outcome["value"] = execute()
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            done.set()

    # The SDK calls on_stdout on this thread; keep the caller's DSPy and MLflow context.
    context = contextvars.copy_context()
    threading.Thread(target=context.run, args=(target,), name="fleet-run-code", daemon=True).start()
    while not done.wait(LIVENESS_POLL_SECONDS):
        if not alive():
            raise SandboxLostError("The sandbox was lost while the execution was running")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]
