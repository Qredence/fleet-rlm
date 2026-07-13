"""Checkpoint version helpers and stale-write errors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fleet_rlm.sessions.errors import SessionRepositoryError


class StaleCheckpointError(SessionRepositoryError):
    """Raised when a commit's expected checkpoint version does not match current."""

    def __init__(
        self,
        session_id: UUID,
        *,
        expected: int,
        actual: int,
    ) -> None:
        super().__init__(f"stale checkpoint for session {session_id}: expected {expected}, actual {actual}")
        self.session_id = session_id
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True, slots=True)
class TurnClaim:
    """Result of claiming a turn (may be a replay of a prior idempotent success)."""

    run_id: UUID
    base_checkpoint_version: int
    replay: bool = False
    assistant_text: str | None = None
    detail_parts: tuple[dict[str, Any], ...] = ()
    structured_output: dict[str, Any] | None = None
    result_schema_id: str | None = None
    result_schema_version: str | None = None
