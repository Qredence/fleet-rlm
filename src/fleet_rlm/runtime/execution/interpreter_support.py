"""Shared helpers and event types for interpreter implementations.

This module contains infrastructure used by the Daytona interpreter
(``integrations/daytona/interpreter.py``). It intentionally stays separate
from the protocol definitions in ``interpreter_protocol.py`` which are
consumed by the tools layer.
"""

from __future__ import annotations

import hashlib
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import dspy

from fleet_rlm.runtime.content.execution_limits import execution_max_text_chars

from .profiles import ExecutionProfile

# ---------------------------------------------------------------------------
# Common interpreter helpers (previously interpreter_common.py)
# ---------------------------------------------------------------------------


def initialize_llm_query_state(
    target: Any,
    *,
    sub_lm: dspy.LM | None,
    max_llm_calls: int,
    llm_call_timeout: int,
) -> None:
    """Populate shared LLM-query state used by interpreter backends."""
    target.sub_lm = sub_lm
    target.max_llm_calls = max_llm_calls
    target.llm_call_timeout = llm_call_timeout
    target._llm_call_count = 0
    target._llm_call_lock = threading.Lock()
    target._sub_lm_executor = None
    target._sub_lm_executor_lock = threading.Lock()


def initialize_sub_rlm_state(
    target: Any,
    *,
    depth: int = 0,
    max_depth: int = 2,
) -> None:
    """Populate recursion-depth state for sub_rlm() calls."""
    target._sub_rlm_depth = depth
    target._sub_rlm_max_depth = max_depth


def initialize_tool_runtime_state(target: Any) -> None:
    """Populate shared tool and execution callback state."""
    target.output_fields = None
    target._tools = {}
    target.execution_event_callback = None


def get_registered_tools(target: Any) -> dict[str, Callable[..., Any]]:
    """Return the registered tool map for an interpreter."""
    return target._tools


def set_registered_tools(
    target: Any,
    value: dict[str, Callable[..., Any]],
) -> None:
    """Replace the registered tool map for an interpreter."""
    target._tools = value


@contextmanager
def execution_profile_context(
    target: Any,
    profile: ExecutionProfile,
):
    """Temporarily override the default execution profile."""
    previous = target.default_execution_profile
    target.default_execution_profile = profile
    try:
        yield target
    finally:
        target.default_execution_profile = previous


def sync_enter(target: Any) -> Any:
    """Start an interpreter for sync context manager usage."""
    target.start()
    return target


def sync_exit(target: Any) -> bool:
    """Shutdown an interpreter for sync context manager usage."""
    target.shutdown()
    return False


async def async_enter(target: Any) -> Any:
    """Start an interpreter for async context manager usage."""
    if target.async_execute:
        await target.astart()
    else:
        target.start()
    return target


async def async_exit(target: Any) -> bool:
    """Shutdown an interpreter for async context manager usage."""
    if target.async_execute:
        await target.ashutdown()
    else:
        target.shutdown()
    return False


# ---------------------------------------------------------------------------
# Execution event types and helpers (previously interpreter_events.py)
# ---------------------------------------------------------------------------


class SupportsExecutionEventCallback(Protocol):
    execution_event_callback: Any


@dataclass(slots=True)
class InterpreterExecutionEventData:
    """Typed execution event payload prior to callback dispatch."""

    phase: str
    timestamp: float
    execution_profile: str
    code_hash: str
    code_preview: str
    duration_ms: int | None = None
    success: bool | None = None
    result_kind: str | None = None
    output_keys: list[str] | None = None
    stdout_preview: str | None = None
    stderr_preview: str | None = None
    error_type: str | None = None
    error: str | None = None
    event_kind: str | None = None
    path: str | None = None
    bytes_total: int | None = None
    bytes_written: int | None = None

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "phase": self.phase,
            "timestamp": self.timestamp,
            "execution_profile": self.execution_profile,
            "code_hash": self.code_hash,
            "code_preview": self.code_preview,
        }
        optional_fields = {
            "duration_ms": self.duration_ms,
            "success": self.success,
            "result_kind": self.result_kind,
            "output_keys": self.output_keys,
            "stdout_preview": self.stdout_preview,
            "stderr_preview": self.stderr_preview,
            "error_type": self.error_type,
            "error": self.error,
            "event_kind": self.event_kind,
            "path": self.path,
            "bytes_total": self.bytes_total,
            "bytes_written": self.bytes_written,
        }
        payload.update(
            {key: value for key, value in optional_fields.items() if value is not None}
        )
        return payload


def summarize_code(code: str) -> tuple[str, str]:
    """Return deterministic code hash and compact preview text."""
    digest = hashlib.sha256((code or "").encode("utf-8")).hexdigest()[:16]
    compact = " ".join((code or "").split())
    limit = execution_max_text_chars()
    if len(compact) > limit:
        compact = f"{compact[:limit]}...[truncated]"
    return digest, compact


def emit_execution_event(
    interpreter: SupportsExecutionEventCallback,
    event_data: InterpreterExecutionEventData,
) -> None:
    """Best-effort execution hook dispatch for observability callbacks."""
    callback = interpreter.execution_event_callback
    if callback is None:
        return
    try:
        callback(event_data.as_payload())
    except Exception:
        # Hook errors must not interfere with interpreter execution.
        return


def start_event_data(
    *, execution_profile: str, code_hash: str, code_preview: str
) -> InterpreterExecutionEventData:
    """Build the standard execution-start event payload."""
    return InterpreterExecutionEventData(
        phase="start",
        timestamp=time.time(),
        execution_profile=execution_profile,
        code_hash=code_hash,
        code_preview=code_preview,
    )


def complete_event_data(
    *,
    started_at: float,
    execution_profile: str,
    code_hash: str,
    code_preview: str,
    success: bool,
    result_kind: str,
    output_keys: list[str] | None = None,
    stdout_preview: str | None = None,
    stderr_preview: str | None = None,
    error_type: str | None = None,
    error: str | None = None,
) -> InterpreterExecutionEventData:
    """Build the standard execution-complete event payload."""
    return InterpreterExecutionEventData(
        phase="complete",
        timestamp=time.time(),
        duration_ms=int((time.time() - started_at) * 1000),
        execution_profile=execution_profile,
        code_hash=code_hash,
        code_preview=code_preview,
        success=success,
        result_kind=result_kind,
        output_keys=output_keys,
        stdout_preview=stdout_preview,
        stderr_preview=stderr_preview,
        error_type=error_type,
        error=error,
    )


def progress_event_data(
    *,
    execution_profile: str,
    code_hash: str,
    code_preview: str,
    event_kind: str,
    path: str | None = None,
    bytes_total: int | None = None,
    bytes_written: int | None = None,
) -> InterpreterExecutionEventData:
    """Build an execution-progress payload for durable-write updates."""
    return InterpreterExecutionEventData(
        phase="progress",
        timestamp=time.time(),
        execution_profile=execution_profile,
        code_hash=code_hash,
        code_preview=code_preview,
        event_kind=event_kind,
        path=path,
        bytes_total=bytes_total,
        bytes_written=bytes_written,
    )
