"""Run lifecycle vocabulary with explicit compatibility aliases.

``turn_lifecycle`` remains the compatibility implementation for now.  The
aliases below let callers migrate by concept without changing state-machine
behavior or the public Turn endpoint.
"""

from __future__ import annotations

from fleet_rlm.chat.turn_lifecycle import (
    BeginTurn,
    CommittedTurnReceipt,
    ExecuteTurn,
    FailedRunReceipt,
    ReplayTurn,
    TurnAlreadyCompletedError,
    TurnFailure,
    TurnFinalization,
    TurnIdempotencyMismatchError,
    TurnInProgressError,
    TurnIntegrityError,
    TurnLifecycle,
    TurnLifecycleError,
    TurnLifecycleService,
    TurnLifecycleUnavailableError,
    TurnNotFoundError,
    TurnStateError,
    TurnValidationError,
    failure_code_for_terminal_status,
)

# Expand-step vocabulary: retain the old names as source-compatible aliases.
RunClaim = BeginTurn
ClaimedRun = ExecuteTurn
CommittedRunReplay = ReplayTurn
RunFailure = TurnFailure
RunSettlement = TurnFinalization
RunLifecycle = TurnLifecycle
RunLifecycleService = TurnLifecycleService
RunLifecycleError = TurnLifecycleError
RunNotFoundError = TurnNotFoundError
RunInProgressError = TurnInProgressError
RunIdempotencyMismatchError = TurnIdempotencyMismatchError
RunValidationError = TurnValidationError
RunStateError = TurnStateError
RunAlreadyCompletedError = TurnAlreadyCompletedError
RunIntegrityError = TurnIntegrityError
RunLifecycleUnavailableError = TurnLifecycleUnavailableError

__all__ = [
    "BeginTurn",
    "ClaimedRun",
    "CommittedRunReplay",
    "CommittedTurnReceipt",
    "ExecuteTurn",
    "FailedRunReceipt",
    "ReplayTurn",
    "RunAlreadyCompletedError",
    "RunClaim",
    "RunFailure",
    "RunIdempotencyMismatchError",
    "RunInProgressError",
    "RunIntegrityError",
    "RunLifecycle",
    "RunLifecycleError",
    "RunLifecycleService",
    "RunLifecycleUnavailableError",
    "RunNotFoundError",
    "RunSettlement",
    "RunStateError",
    "RunValidationError",
    "TurnAlreadyCompletedError",
    "TurnFailure",
    "TurnFinalization",
    "TurnIdempotencyMismatchError",
    "TurnInProgressError",
    "TurnIntegrityError",
    "TurnLifecycle",
    "TurnLifecycleError",
    "TurnLifecycleService",
    "TurnLifecycleUnavailableError",
    "TurnNotFoundError",
    "TurnStateError",
    "TurnValidationError",
    "failure_code_for_terminal_status",
]
