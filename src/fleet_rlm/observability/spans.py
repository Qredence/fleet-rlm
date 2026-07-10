"""Provider-neutral span records rendered through the existing runtime event."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fleet_rlm.runtime.events import RuntimeEvent


@dataclass(frozen=True, slots=True)
class RuntimeTraceSpan:
    """An internal span that preserves the public ``MLFLOW_SPAN`` event shape."""

    span_id: str
    name: str
    status: str
    trace_id: str | None = None
    parent_span_id: str | None = None
    duration_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_runtime_event(self) -> RuntimeEvent:
        return RuntimeEvent.mlflow_span(
            span_id=self.span_id,
            name=self.name,
            status=self.status,
            trace_id=self.trace_id,
            parent_span_id=self.parent_span_id,
            duration_ms=self.duration_ms,
            metadata=self.metadata or None,
        )


__all__ = ["RuntimeTraceSpan"]
