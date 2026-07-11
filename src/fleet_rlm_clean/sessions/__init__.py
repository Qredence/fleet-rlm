"""Session domain for clean-backend durable conversation state."""

from __future__ import annotations

from fleet_rlm_clean.sessions.checkpoints import StaleCheckpointError, TurnClaim
from fleet_rlm_clean.sessions.errors import (
    IdempotencyConflictError,
    SessionNotFoundError,
    SessionRepositoryError,
)
from fleet_rlm_clean.sessions.history import history_message_count, turns_to_history
from fleet_rlm_clean.sessions.locks import SessionLockRegistry
from fleet_rlm_clean.sessions.models import SessionRecord, SessionSnapshot
from fleet_rlm_clean.sessions.repository import SessionRepository

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
