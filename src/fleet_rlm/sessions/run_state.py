"""Run claim values, receipts, and lifecycle errors shared below chat coordination."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, TypeAlias
from uuid import UUID

from fleet_rlm.artifacts.models import ArtifactRef
from fleet_rlm.runtime.authority import RunAuthority
from fleet_rlm.runtime.usage import RLMUsage
from fleet_rlm.sessions.committed_turn import CommittedTurn
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
from fleet_rlm.sessions.run_claim import ClaimFailure, ClaimFailureCode

RunFailureCode: TypeAlias = ClaimFailureCode


class RunLifecycleError(RuntimeError):
    """Base class for safe lifecycle failures."""


class RunNotFoundError(RunLifecycleError):
    pass


class RunInProgressError(RunLifecycleError):
    pass


class RunIdempotencyMismatchError(RunLifecycleError):
    pass


class RunValidationError(RunLifecycleError):
    pass


class RunStateError(RunLifecycleError):
    pass


class RunAlreadyCompletedError(RunStateError):
    """The Run already committed; late claim work targeting it is a benign no-op."""


class RunIntegrityError(RunLifecycleError):
    pass


class RunLifecycleUnavailableError(RunLifecycleError):
    pass


@dataclass(frozen=True, slots=True)
class RunClaim:
    access: TurnAccess
    session_id: UUID
    input: TurnInput
    idempotency_key: str
    proposed_run_id: UUID


@dataclass(frozen=True, slots=True)
class _RunClaimToken:
    value: UUID
    base_checkpoint_version: int = 0


@dataclass(frozen=True, slots=True)
class ClaimedRun:
    run_id: UUID
    session_id: UUID
    access: TurnAccess
    input: TurnInput
    history: SessionHistory
    cancellation_requested: Callable[[], Awaitable[bool]]
    _claim: _RunClaimToken
    authority: RunAuthority = field(default_factory=RunAuthority, compare=False, repr=False)

    @property
    def checkpoint_version(self) -> int:
        """Checkpoint from which this Turn was claimed."""
        return self._claim.base_checkpoint_version


@dataclass(frozen=True, slots=True)
class CommittedRunReplay:
    run_id: UUID
    session_id: UUID
    committed_turn: CommittedTurn
    checkpoint_version: int


RunStart: TypeAlias = ClaimedRun | CommittedRunReplay


@dataclass(frozen=True, slots=True)
class RunFailure:
    terminal_status: Literal["failed", "cancelled", "timeout"]
    failure_code: RunFailureCode
    public_message: str
    usage: RLMUsage


def _claim_failure(failure: RunFailure) -> ClaimFailure:
    return ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message)


@dataclass(frozen=True, slots=True)
class CommittedTurnReceipt:
    run_id: UUID
    checkpoint_version: int
    committed_turn: CommittedTurn
    artifacts: tuple[ArtifactRef, ...]


@dataclass(frozen=True, slots=True)
class FailedRunReceipt:
    run_id: UUID
    terminal_status: Literal["failed", "cancelled", "timeout"]
    failure_code: RunFailureCode
    public_message: str
    durable: bool


RunSettlement: TypeAlias = CommittedTurnReceipt | FailedRunReceipt
CancelResult: TypeAlias = Literal["requested", "already_requested", "already_terminal"]


__all__ = [
    "CancelResult",
    "ClaimedRun",
    "CommittedRunReplay",
    "CommittedTurnReceipt",
    "FailedRunReceipt",
    "RunAlreadyCompletedError",
    "RunClaim",
    "RunFailure",
    "RunFailureCode",
    "RunIdempotencyMismatchError",
    "RunInProgressError",
    "RunIntegrityError",
    "RunLifecycleError",
    "RunLifecycleUnavailableError",
    "RunNotFoundError",
    "RunSettlement",
    "RunStart",
    "RunStateError",
    "RunValidationError",
    "_RunClaimToken",
    "_claim_failure",
]
