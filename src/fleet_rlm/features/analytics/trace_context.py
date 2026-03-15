"""Trace context utilities for LLM analytics."""

from __future__ import annotations

import contextvars
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(slots=True)
class LLMTraceContext:
    """Trace metadata captured for one LM call."""

    trace_id: str
    call_id: str
    start_time: float
    model: str | None = None
    provider: str | None = None
    parent_trace_id: str | None = None

    @classmethod
    def create(cls, *, call_id: str, parent_trace_id: str | None) -> "LLMTraceContext":
        return cls(
            trace_id=str(uuid.uuid4()),
            call_id=call_id,
            start_time=time.monotonic(),
            parent_trace_id=parent_trace_id,
        )


_CURRENT_TRACE: contextvars.ContextVar[LLMTraceContext | None] = contextvars.ContextVar(
    "fleet_rlm_current_trace",
    default=None,
)
_RUNTIME_DISTINCT_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "fleet_rlm_runtime_distinct_id",
    default=None,
)
_RUNTIME_TELEMETRY_ENABLED: contextvars.ContextVar[bool | None] = (
    contextvars.ContextVar(
        "fleet_rlm_runtime_telemetry_enabled",
        default=None,
    )
)


def get_current_trace() -> LLMTraceContext | None:
    """Get the active trace in the current execution context."""
    return _CURRENT_TRACE.get()


def push_current_trace(
    trace: LLMTraceContext,
) -> contextvars.Token[LLMTraceContext | None]:
    """Push an active trace onto context-local storage."""
    return _CURRENT_TRACE.set(trace)


def pop_current_trace(token: contextvars.Token[LLMTraceContext | None]) -> None:
    """Restore the previous trace state for this execution context."""
    _CURRENT_TRACE.reset(token)


def get_runtime_distinct_id() -> str | None:
    """Return the runtime scoped distinct_id, if one was set."""
    value = _RUNTIME_DISTINCT_ID.get()
    if value is None:
        return None
    candidate = value.strip()
    return candidate or None


def get_runtime_telemetry_enabled() -> bool | None:
    """Return the runtime scoped telemetry preference, if one was set."""
    return _RUNTIME_TELEMETRY_ENABLED.get()


@contextmanager
def runtime_distinct_id_context(distinct_id: str | None):
    """Temporarily scope an analytics distinct_id to this runtime context."""
    token = _RUNTIME_DISTINCT_ID.set(distinct_id)
    try:
        yield
    finally:
        _RUNTIME_DISTINCT_ID.reset(token)


@contextmanager
def runtime_telemetry_enabled_context(enabled: bool | None):
    """Temporarily scope a runtime telemetry preference to this execution context."""
    token = _RUNTIME_TELEMETRY_ENABLED.set(enabled)
    try:
        yield
    finally:
        _RUNTIME_TELEMETRY_ENABLED.reset(token)
