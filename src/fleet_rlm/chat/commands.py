"""Validated application commands for Session-first Turns."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fleet_rlm.sessions.models import TurnAccess, TurnInput


@dataclass(frozen=True, slots=True)
class OpenTurnCommand:
    """Canonical Turn intent after local-scope and schema validation."""

    access: TurnAccess
    session_id: UUID
    input: TurnInput
    idempotency_key: str
    proposed_run_id: UUID

    def __post_init__(self) -> None:
        key = self.idempotency_key
        if (
            not isinstance(key, str)
            or not 1 <= len(key) <= 128
            or key != key.strip()
            or not key.isprintable()
            or any(char.isspace() for char in key)
        ):
            raise ValueError("idempotency_key must contain 1..128 printable non-whitespace characters")
