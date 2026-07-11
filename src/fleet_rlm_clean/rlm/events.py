"""Public RuntimeEvent v1 kinds and per-run event recording."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal, Mapping
from uuid import UUID, uuid4


class RuntimeEventKind(StrEnum):
    """Foundation event kinds for the clean-backend SSE contract."""

    RUN_STARTED = "run.started"
    STATUS = "status"
    TEXT_DELTA = "text.delta"
    TEXT_COMPLETED = "text.completed"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    SKILL_LOADED = "skill.loaded"
    ATTACHMENT_READ = "attachment.read"
    ARTIFACT_CREATED = "artifact.created"
    USAGE = "usage"
    WARNING = "warning"
    ERROR = "error"
    RUN_COMPLETED = "run.completed"


TERMINAL_KINDS: frozenset[RuntimeEventKind] = frozenset(
    {
        RuntimeEventKind.ERROR,
        RuntimeEventKind.RUN_COMPLETED,
    }
)


class DuplicateTerminalEventError(RuntimeError):
    """Raised when a second terminal event is emitted for the same run."""


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Immutable public event envelope (schema_version=1)."""

    schema_version: Literal[1]
    event_id: UUID
    run_id: UUID
    session_id: UUID
    sequence: int
    timestamp: datetime
    kind: RuntimeEventKind
    payload: Mapping[str, Any]


@dataclass(slots=True)
class EventRecorder:
    """Assigns strictly increasing sequence numbers for one run."""

    run_id: UUID
    session_id: UUID
    _sequence: int = field(default=0, init=False, repr=False)
    _terminal_emitted: bool = field(default=False, init=False, repr=False)

    def emit(self, kind: RuntimeEventKind, payload: Mapping[str, Any] | None = None) -> RuntimeEvent:
        """Emit the next public event for this run."""
        if self._terminal_emitted:
            msg = f"run {self.run_id} already emitted a terminal event"
            raise DuplicateTerminalEventError(msg)
        self._sequence += 1
        event = RuntimeEvent(
            schema_version=1,
            event_id=uuid4(),
            run_id=self.run_id,
            session_id=self.session_id,
            sequence=self._sequence,
            timestamp=datetime.now(UTC),
            kind=kind,
            payload=MappingProxyType(dict(payload or {})),
        )
        if kind in TERMINAL_KINDS:
            self._terminal_emitted = True
        return event
