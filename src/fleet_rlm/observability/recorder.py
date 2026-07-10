"""Incremental recorder around the canonical runtime event stream."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import uuid4

from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind

from .events import RecordedRuntimeEvent
from .redaction import sanitize_runtime_event
from .token_usage import TokenUsage, token_usage_from_mapping


@dataclass(frozen=True, slots=True)
class RuntimeTracePerformance:
    """Compact performance counters exposed by a finalized trace record."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class RuntimeTraceRecord:
    """A finalized, client-safe record of one backend turn."""

    trace_id: str
    session_id: str | None
    execution_backend: str
    events: tuple[RecordedRuntimeEvent, ...]
    terminal_kind: Literal[RuntimeEventKind.DONE, RuntimeEventKind.ERROR]
    started_at: datetime
    ended_at: datetime
    duration_ms: int
    performance: RuntimeTracePerformance


class RuntimeTraceRecorder:
    """Observe runtime events incrementally and finalize exactly once.

    ``observe()`` returns transport-safe event(s). On direct-RLM terminal
    events, it inserts one completion `MLFLOW_SPAN` before the terminal event
    so existing SSE/WebSocket projectors can keep their terminal semantics.
    """

    def __init__(self, *, session_id: str | None, execution_backend: str) -> None:
        self._session_id = session_id
        self._execution_backend = execution_backend
        self._trace_id = str(uuid4())
        self._started_at = datetime.now(UTC)
        self._events: list[RecordedRuntimeEvent] = []
        self._usage = TokenUsage()
        self._record: RuntimeTraceRecord | None = None

    @property
    def finalized(self) -> bool:
        return self._record is not None

    @property
    def record(self) -> RuntimeTraceRecord | None:
        return self._record

    def observe(self, event: RuntimeEvent) -> tuple[RuntimeEvent, ...]:
        """Record and sanitize an event before any transport projection."""
        safe_event = sanitize_runtime_event(event)
        if self._record is not None:
            return (safe_event,)

        self._events.append(RecordedRuntimeEvent.from_runtime_event(safe_event))
        self._usage = self._usage.add(token_usage_from_mapping(safe_event.payload))

        if not safe_event.kind.is_terminal():
            return (safe_event,)

        record = self._finalize(cast(Literal[RuntimeEventKind.DONE, RuntimeEventKind.ERROR], safe_event.kind))
        if self._execution_backend != "direct_rlm":
            return (safe_event,)

        from .mlflow import emit_direct_rlm_completion_span

        completion_span = sanitize_runtime_event(emit_direct_rlm_completion_span(record).to_runtime_event())
        return (completion_span, safe_event)

    def _finalize(
        self,
        terminal_kind: Literal[RuntimeEventKind.DONE, RuntimeEventKind.ERROR],
    ) -> RuntimeTraceRecord:
        ended_at = datetime.now(UTC)
        duration_ms = max(0, int((ended_at - self._started_at).total_seconds() * 1000))
        record = RuntimeTraceRecord(
            trace_id=self._trace_id,
            session_id=self._session_id,
            execution_backend=self._execution_backend,
            events=tuple(self._events),
            terminal_kind=terminal_kind,
            started_at=self._started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            performance=RuntimeTracePerformance(
                input_tokens=self._usage.input_tokens,
                output_tokens=self._usage.output_tokens,
                total_tokens=self._usage.total_tokens,
            ),
        )
        self._record = record
        return record


__all__ = ["RuntimeTracePerformance", "RuntimeTraceRecord", "RuntimeTraceRecorder"]
