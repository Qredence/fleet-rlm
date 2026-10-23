"""Host side of the Daytona REPL transport: Daytona interpreter contexts and the stdout bridge protocol.

Each Fleet namespace is one ``code_interpreter`` context, prepared once with the Skill and workspace
helpers plus ``runtime/repl.py``. An execution is one ``run_code`` call. The sandbox reports host-tool
calls and the final result as marker lines on stdout (see ``runtime/repl.py``); host calls run on their
own threads so the websocket keeps reading, and replies go back through ``fs.upload_file``.
"""

from __future__ import annotations

import json
import logging
import math
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
PROGRESS_MARK = "\x1eFLEET_PROGRESS "
REPLY_DIR = "/tmp/fleet/replies"
_PREAMBLE_TIMEOUT_SECONDS = 60
_RETRY_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 0.5
_MAX_PROGRESS_LABEL = 64
_PROGRESS_PHASES = frozenset({"start", "checkpoint", "exit"})


def _bounded_progress(payload: Any) -> dict[str, Any] | None:
    """Validate one progress observation from the sandbox and keep only bounded scalar fields."""
    if not isinstance(payload, dict):
        return None
    phase = payload.get("phase")
    scope = payload.get("scope")
    if phase not in _PROGRESS_PHASES or not isinstance(scope, str) or not scope:
        return None
    expectation = payload.get("expect_seconds")
    if isinstance(expectation, bool) or not isinstance(expectation, (int, float)):
        return None
    if not math.isfinite(float(expectation)) or float(expectation) <= 0:
        return None
    checkpoint = payload.get("checkpoint")
    if checkpoint is not None and (isinstance(checkpoint, bool) or not isinstance(checkpoint, int) or checkpoint < 0):
        return None
    return {
        "phase": phase,
        "scope": scope[:_MAX_PROGRESS_LABEL],
        "expect_seconds": float(expectation),
        "checkpoint": checkpoint,
    }


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


class ExecutionFence:
    """Publication liveness for one sandbox execution.

    One execution owns one token. Closing it is one-way: the originating Turn's cancellation and
    the end of the execution both close it, and a later Turn can never reopen it. The reusable
    root cancellation Event is therefore not the fence's source of truth, because a new Turn
    clears that Event for its own work.
    """

    def __init__(self, emitter: Callable[[dict[str, Any]], None] | None = None) -> None:
        """Open one execution's publication window on the emitter that owns it.

        `emitter` is the event channel of the Turn that opened the execution. Capturing it here,
        instead of reading the manager's mutable callback when a refusal happens, keeps a late
        fence attributed to the execution that produced the reply rather than to a later Turn.
        """
        self._closed = threading.Event()
        self.emitter = emitter

    def is_open(self) -> bool:
        """Return True while this execution may still publish a successful reply."""
        return not self._closed.is_set()

    def close(self) -> None:
        """Stop publication for this execution forever; repeated calls are harmless."""
        self._closed.set()


class _PublicationFencedError(Exception):
    """Internal control signal: a successful reply lost its execution identity before publication.

    This is not a Turn outcome and never leaves the host-call worker; cancellation itself stays the
    ordinary `RuntimeError` raised by `check_turn()`.
    """


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

    def __init__(
        self,
        sandbox: Any,
        on_call: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        fence: ExecutionFence | None = None,
        on_fence: Callable[[], None] | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.on_call = on_call
        self.result: dict[str, Any] | None = None
        self.fence = fence if fence is not None else ExecutionFence()
        self._on_fence = on_fence
        self._on_progress = on_progress
        self._buffer = ""
        self._workers: list[threading.Thread] = []

    def feed(self, message: Any) -> None:
        self._buffer += message.output
        *lines, self._buffer = self._buffer.split("\n")
        for line in lines:
            self._line(line)

    def finish(self) -> None:
        if self._buffer:
            self._line(self._buffer)
            self._buffer = ""
        for worker in self._workers:
            worker.join(timeout=5)
        # The execution identity ends here. A worker the join abandoned must not publish into a
        # later execution, so its token is closed before this execution returns.
        self.fence.close()

    def _line(self, line: str) -> None:
        if line.startswith(RESULT_MARK):
            self.result = json.loads(line[len(RESULT_MARK) :])
        elif line.startswith(CALL_MARK):
            call = json.loads(line[len(CALL_MARK) :])
            worker = threading.Thread(target=self._answer, args=(call,), name="fleet-host-call", daemon=True)
            self._workers.append(worker)
            worker.start()
        elif line.startswith(PROGRESS_MARK):
            # Progress is observation only. A malformed marker or a failing consumer is dropped;
            # it must never disturb the execution or the stream.
            try:
                progress = _bounded_progress(json.loads(line[len(PROGRESS_MARK) :]))
                if progress is not None and self._on_progress is not None:
                    self._on_progress(progress)
            except Exception:
                _LOGGER.debug("Dropped a progress observation")

    def _answer(self, call: dict[str, Any]) -> None:
        # A host-call worker owns no Turn; nothing may escape it into the transport thread.
        try:
            self._send_reply(call)
        except Exception:
            _LOGGER.warning("A host tool call worker failed before it could answer")

    def _send_reply(self, call: dict[str, Any]) -> None:
        try:
            reply = self.on_call(call)
        except Exception:
            # Deliver an error reply rather than leaving the sandbox waiting for the full bound.
            reply = {"error": "Host tool call failed"}
        if not isinstance(reply, dict):
            reply = {"error": "Host tool reply was not an object"}
        successful = "error" not in reply
        try:
            body = json.dumps(reply, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            body = json.dumps({"error": "Host tool result is not JSON-serializable"}).encode("utf-8")
            successful = False
        path = f"{REPLY_DIR}/{call['id']}.json"

        def upload() -> None:
            # The fence is checked immediately before every attempt, so a retry after a transient
            # failure cannot publish a success whose execution has since been cancelled or ended.
            if successful and not self.fence.is_open():
                raise _PublicationFencedError
            self.sandbox.fs.upload_file(body, path)

        try:
            with_retries(upload)
        except _PublicationFencedError:
            self._refuse_publication()
        except Exception:
            # The sandbox call times out (host_call_timeout) and the model sees the error.
            _LOGGER.warning("Could not return a host tool result to the sandbox")

    def _refuse_publication(self) -> None:
        # One bounded warning per refused publication: no tool payload, no generated source.
        _LOGGER.warning("Fenced a successful host tool result after Turn cancellation")
        if self._on_fence is not None:
            try:
                self._on_fence()
            except Exception:
                _LOGGER.debug("Could not report a fenced host tool result")


def run(
    sandbox: Any,
    context: Any,
    payload: dict[str, Any],
    *,
    on_call: Callable[[dict[str, Any]], dict[str, Any]],
    timeout: int | None,
    fence: ExecutionFence | None = None,
    on_fence: Callable[[], None] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run one execution in `context`; return the runtime's result dict.

    `timeout` is the Turn's remaining deadline in whole seconds, or None when the Turn is
    deadline-free. It is forwarded to the sandbox as `host_call_timeout`, so a bridged host call
    waits at most as long as the Turn may still run; a deadline-free Turn sends an explicit
    absence of a bound and no component substitutes one. Daytona's `run_code` timeout is passed
    explicitly, as 0 for a deadline-free Turn, because omitting it would leave the SDK default
    in charge of the Turn.

    `fence` is this execution's publication token. The manager closes it when the Turn is
    cancelled; this run also closes it when the execution ends. A successful host-call result is
    published only while the token is open, and the token is checked again before every upload
    attempt. Error and cancellation replies are never fenced.

    Raises the SDK's error when the transport fails; the caller decides whether the outcome is known.
    """
    protocol = _Protocol(sandbox, on_call, fence=fence, on_fence=on_fence, on_progress=on_progress)
    # The Turn's remaining deadline is the only production source of this bound. A value carried by
    # the payload is a test seam for shortening it; nothing may substitute a fixed fallback.
    payload = {"host_call_timeout": timeout, **payload}
    code = f"_fleet_exec({json.dumps(payload, separators=(',', ':'))!r})"
    try:
        execution = sandbox.code_interpreter.run_code(
            code,
            context=context,
            on_stdout=protocol.feed,
            timeout=0 if timeout is None else timeout,
        )
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
