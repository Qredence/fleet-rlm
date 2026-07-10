"""Internal, provider-neutral representations of recorded runtime events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind


@dataclass(frozen=True, slots=True)
class RecordedRuntimeEvent:
    """A sanitized runtime event retained by a trace recorder.

    This mirrors the canonical event *instance*, rather than duplicating
    ``RuntimeEventKind`` or its public transport schema.
    """

    kind: RuntimeEventKind
    text: str
    payload: dict[str, Any]
    timestamp: datetime

    @classmethod
    def from_runtime_event(cls, event: RuntimeEvent) -> RecordedRuntimeEvent:
        return cls(
            kind=event.kind,
            text=event.text,
            payload=dict(event.payload),
            timestamp=event.timestamp,
        )


__all__ = ["RecordedRuntimeEvent"]
