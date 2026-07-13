"""Domain records returned by SessionRepository (not ORM rows)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: UUID
    user_id: UUID
    workspace_id: UUID
    status: str
    title: str
    checkpoint_version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TurnRecord:
    id: UUID
    session_id: UUID
    sequence: int
    role: str
    content: str
    status: str
    run_id: UUID | None = None
    detail_parts: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    structured_output: dict[str, Any] | None = None
    result_schema_id: str | None = None
    result_schema_version: str | None = None


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    """Loaded session state for a turn (History reconstruction lands in impl-06)."""

    session: SessionRecord
    turns: tuple[TurnRecord, ...] = field(default_factory=tuple)
