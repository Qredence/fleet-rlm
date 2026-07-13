"""Session domain for Fleet RLM durable conversation state."""

from __future__ import annotations

from fleet_rlm.sessions.checkpoints import StaleCheckpointError, TurnClaim
from fleet_rlm.sessions.errors import (
    IdempotencyConflictError,
    SessionNotFoundError,
    SessionRepositoryError,
)
from fleet_rlm.sessions.history import history_message_count, turns_to_history
from fleet_rlm.sessions.locks import SessionLockRegistry
from fleet_rlm.sessions.models import SessionRecord, SessionSnapshot
from fleet_rlm.sessions.repository import SessionRepository

__all__ = [
    "IdempotencyConflictError",
    "SessionLockRegistry",
    "SessionNotFoundError",
    "SessionRecord",
    "SessionRepository",
    "SessionRepositoryError",
    "SessionSnapshot",
    "StaleCheckpointError",
    "TurnClaim",
    "history_message_count",
    "turns_to_history",
]
