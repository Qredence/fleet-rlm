"""Execution event helpers for :mod:`fleet_rlm.core.execution.interpreter`."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Protocol

from fleet_rlm.features.logs.execution_limits import execution_max_text_chars


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
