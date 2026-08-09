"""Run preparation vocabulary with compatibility aliases."""

from __future__ import annotations

from fleet_rlm.chat.turn_preparation import (
    DefaultTurnPreparer,
    PreparedTurn,
    TurnPreparation,
    TurnPreparationCancelledError,
    TurnPreparationError,
    TurnPreparationIntegrityError,
    TurnPreparationTimeoutError,
    TurnPreparationUnavailableError,
    TurnPreparationValidationError,
)

PreparedRun = PreparedTurn
RunPreparation = TurnPreparation
DefaultRunPreparer = DefaultTurnPreparer
RunPreparationError = TurnPreparationError
RunPreparationCancelledError = TurnPreparationCancelledError
RunPreparationTimeoutError = TurnPreparationTimeoutError
RunPreparationValidationError = TurnPreparationValidationError
RunPreparationIntegrityError = TurnPreparationIntegrityError
RunPreparationUnavailableError = TurnPreparationUnavailableError

__all__ = [
    "DefaultRunPreparer",
    "DefaultTurnPreparer",
    "PreparedRun",
    "PreparedTurn",
    "RunPreparation",
    "RunPreparationCancelledError",
    "RunPreparationError",
    "RunPreparationIntegrityError",
    "RunPreparationTimeoutError",
    "RunPreparationUnavailableError",
    "RunPreparationValidationError",
    "TurnPreparation",
    "TurnPreparationCancelledError",
    "TurnPreparationError",
    "TurnPreparationIntegrityError",
    "TurnPreparationTimeoutError",
    "TurnPreparationUnavailableError",
    "TurnPreparationValidationError",
]
