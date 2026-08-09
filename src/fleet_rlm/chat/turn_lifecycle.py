"""Compatibility imports for the pre-Phase-1 Turn lifecycle module."""

from __future__ import annotations

from fleet_rlm.chat.run_lifecycle import (
    CancelResult,
    ClaimedRun,
    CommittedRunReplay,
    CommittedTurnReceipt,
    FailedRunReceipt,
    RunAlreadyCompletedError,
    RunClaim,
    RunFailure,
    RunFailureCode,
    RunIdempotencyMismatchError,
    RunInProgressError,
    RunIntegrityError,
    RunLifecycle,
    RunLifecycleError,
    RunLifecycleService,
    RunLifecycleUnavailableError,
    RunNotFoundError,
    RunSettlement,
    RunStart,
    RunStateError,
    RunValidationError,
    _RunClaimToken,
    _RunStateStore,
    failure_code_for_terminal_status,
)

TurnLifecycleError = RunLifecycleError
TurnNotFoundError = RunNotFoundError
TurnInProgressError = RunInProgressError
TurnIdempotencyMismatchError = RunIdempotencyMismatchError
TurnValidationError = RunValidationError
TurnStateError = RunStateError
TurnAlreadyCompletedError = RunAlreadyCompletedError
TurnIntegrityError = RunIntegrityError
TurnLifecycleUnavailableError = RunLifecycleUnavailableError
BeginTurn = RunClaim
ExecuteTurn = ClaimedRun
ReplayTurn = CommittedRunReplay
TurnStart = RunStart
FailureCode = RunFailureCode
TurnFailure = RunFailure
TurnFinalization = RunSettlement
TurnLifecycle = RunLifecycle
TurnLifecycleService = RunLifecycleService
_TurnClaimToken = _RunClaimToken
_TurnStateStore = _RunStateStore

__all__ = [
    "BeginTurn",
    "CancelResult",
    "CommittedTurnReceipt",
    "ExecuteTurn",
    "FailedRunReceipt",
    "FailureCode",
    "ReplayTurn",
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
    "TurnStart",
    "TurnStateError",
    "TurnValidationError",
    "failure_code_for_terminal_status",
]
