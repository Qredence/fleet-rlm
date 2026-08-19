"""Read-oriented Session catalog domain values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from fleet_rlm.sessions.models import (
    AssistantTurnRecord,
    SessionRecord,
    UserTurnRecord,
)


@dataclass(frozen=True, slots=True)
class SequenceCursor:
    """An actual append-only Turn sequence cursor, never an offset."""

    after_sequence: int | None = None

    def __post_init__(self) -> None:
        if self.after_sequence is not None and (
            not isinstance(self.after_sequence, int) or isinstance(self.after_sequence, bool) or self.after_sequence < 0
        ):
            raise ValueError("after_sequence must be a non-negative integer")

    def next_after_sequence(self, last_sequence: int) -> int:
        if not isinstance(last_sequence, int) or isinstance(last_sequence, bool) or last_sequence < 1:
            raise ValueError("last_sequence must be a positive integer")
        if self.after_sequence is not None and last_sequence <= self.after_sequence:
            raise ValueError("next sequence must advance the cursor")
        return last_sequence


@dataclass(frozen=True, slots=True)
class SessionPage:
    items: tuple[SessionRecord, ...]
    total: int


@dataclass(frozen=True, slots=True)
class SessionTurnPage:
    items: tuple[UserTurnRecord | AssistantTurnRecord, ...]
    next_after_sequence: int | None


class SessionCatalog(Protocol):
    async def create(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        title: str,
    ) -> SessionRecord: ...

    async def list(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        status: str | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> SessionPage: ...

    async def get(self, session_id: UUID, *, user_id: UUID, workspace_id: UUID) -> SessionRecord: ...

    async def update(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        title: str | None,
        status: str | None,
    ) -> SessionRecord: ...

    async def archive(self, session_id: UUID, *, user_id: UUID, workspace_id: UUID) -> SessionRecord: ...

    async def turns(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        cursor: SequenceCursor,
        limit: int,
    ) -> SessionTurnPage: ...
