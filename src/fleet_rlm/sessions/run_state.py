"""Run claim values, receipts, and lifecycle errors shared below chat coordination."""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, TypeAlias, assert_never
from uuid import UUID

from fleet_rlm.artifacts.models import ArtifactRef
from fleet_rlm.sessions.committed_turn import CommittedTurn
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
from fleet_rlm.sessions.usage import RLMUsage

ClaimStatus = Literal["running", "settling", "completed", "failed", "cancelled", "timeout"]
ClaimTerminalStatus = Literal["failed", "cancelled", "timeout"]
ClaimFailureCode = Literal[
    "preparation_failed",
    "execution_failed",
    "commit_failed",
    "cancelled",
    "timeout",
    "stale_claim",
]


@dataclass(frozen=True, slots=True)
class ClaimFailure:
    status: ClaimTerminalStatus
    code: ClaimFailureCode
    public_message: str


@dataclass(frozen=True, slots=True)
class ClaimState:
    status: ClaimStatus
    failure_code: ClaimFailureCode | None = None
    intent: ClaimFailure | None = None


@dataclass(frozen=True, slots=True)
class ClaimTransition:
    status: ClaimTerminalStatus
    failure_code: ClaimFailureCode
    public_message: str
    finalized: bool
    next_state: ClaimState | None = None


@dataclass(frozen=True, slots=True)
class FailClaim:
    failure: ClaimFailure
    usage: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BeginSettlement:
    failure: ClaimFailure
    usage: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RevokeClaim:
    failure: ClaimFailure
    usage: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompleteSettlement:
    pass


@dataclass(frozen=True, slots=True)
class HeartbeatClaim:
    pass


ClaimCommand: TypeAlias = FailClaim | BeginSettlement | RevokeClaim | CompleteSettlement | HeartbeatClaim


@dataclass(frozen=True, slots=True)
class ClaimDecision:
    transition: ClaimTransition | None = None
    heartbeat_allowed: bool = False


class InvalidClaimTransitionError(ValueError):
    """The requested action is invalid for the current durable state."""


def decide_claim_transition(state: ClaimState, command: ClaimCommand) -> ClaimDecision:
    """Return the durable claim decision without performing I/O."""
    match command:
        case FailClaim(failure):
            return ClaimDecision(transition=_fail(state, failure))
        case BeginSettlement(failure):
            return ClaimDecision(transition=_settle(state, failure))
        case RevokeClaim(failure):
            return ClaimDecision(transition=_revoke(state, failure))
        case CompleteSettlement():
            return ClaimDecision(transition=_complete(state))
        case HeartbeatClaim():
            return ClaimDecision(heartbeat_allowed=state.status in {"running", "settling"})
        case _:
            assert_never(command)


def _fail(state: ClaimState, failure: ClaimFailure) -> ClaimTransition:
    if state.status == "completed":
        raise InvalidClaimTransitionError("a committed Run cannot be failed")
    if state.status == "running":
        next_state = ClaimState(failure.status, failure.code)
        return ClaimTransition(failure.status, failure.code, failure.public_message, True, next_state)
    if state.status == "settling" and state.intent is not None:
        intent = state.intent
        return ClaimTransition(intent.status, intent.code, intent.public_message, False)
    return ClaimTransition(
        _terminal_status(state.status),
        _failure_code(state),
        failure.public_message,
        True,
    )


def _settle(state: ClaimState, failure: ClaimFailure) -> ClaimTransition:
    if state.status == "completed":
        raise InvalidClaimTransitionError("a committed Run cannot be settled")
    if state.status == "running":
        next_state = ClaimState("settling", failure.code, failure)
        return ClaimTransition(failure.status, failure.code, failure.public_message, False, next_state)
    if state.status == "settling":
        intent = state.intent or failure
        return ClaimTransition(intent.status, intent.code, intent.public_message, False)
    return ClaimTransition(
        _terminal_status(state.status),
        _failure_code(state),
        failure.public_message,
        True,
    )


def _revoke(state: ClaimState, failure: ClaimFailure) -> ClaimTransition:
    if state.status == "completed":
        raise InvalidClaimTransitionError("a committed Run cannot be revoked")
    if state.status == "failed" and state.failure_code == "stale_claim":
        return ClaimTransition("failed", "stale_claim", "Turn failed", True)
    if state.status == "running":
        intent = ClaimFailure("failed", "stale_claim", failure.public_message)
        return ClaimTransition(
            "failed",
            "stale_claim",
            failure.public_message,
            False,
            ClaimState("settling", "stale_claim", intent),
        )
    if state.status != "settling":
        raise InvalidClaimTransitionError("a terminal Run cannot be revoked")
    return ClaimTransition("failed", "stale_claim", (state.intent or failure).public_message, False)


def _complete(state: ClaimState) -> ClaimTransition:
    if state.status == "failed" and state.failure_code == "stale_claim":
        return ClaimTransition("failed", "stale_claim", "Turn failed", True)
    if state.status != "settling" or state.intent is None:
        raise InvalidClaimTransitionError("Turn is not settling under this claim")
    intent = state.intent
    return ClaimTransition(
        intent.status,
        intent.code,
        intent.public_message,
        True,
        ClaimState(intent.status, intent.code),
    )


def _terminal_status(status: ClaimStatus) -> ClaimTerminalStatus:
    if status not in {"failed", "cancelled", "timeout"}:
        raise InvalidClaimTransitionError("persisted Run has an invalid failure status")
    return status


def failure_code_for_terminal_status(status: ClaimTerminalStatus) -> ClaimFailureCode:
    """Derive the canonical failure code for one terminal status without intent state."""
    codes: dict[ClaimTerminalStatus, ClaimFailureCode] = {
        "failed": "execution_failed",
        "cancelled": "cancelled",
        "timeout": "timeout",
    }
    return codes[status]


def _failure_code(state: ClaimState) -> ClaimFailureCode:
    if state.failure_code is not None:
        return state.failure_code
    return failure_code_for_terminal_status(_terminal_status(state.status))


RunFailureCode: TypeAlias = ClaimFailureCode


class RunAuthority:
    """Fence host effects after a durable Run claim loses authority."""

    __slots__ = ("_listeners", "_revoked")

    def __init__(self) -> None:
        self._revoked = False
        self._listeners: list[Callable[[], None]] = []

    @property
    def revoked(self) -> bool:
        return self._revoked

    def is_live(self) -> bool:
        return not self._revoked

    def add_revoke_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        if self._revoked:
            listener()
            return lambda: None
        self._listeners.append(listener)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return remove

    def revoke(self) -> None:
        if self._revoked:
            return
        self._revoked = True
        listeners = tuple(self._listeners)
        self._listeners.clear()
        for listener in listeners:
            try:
                listener()
            except BaseException:
                continue


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
    "BeginSettlement",
    "CancelResult",
    "ClaimCommand",
    "ClaimDecision",
    "ClaimFailure",
    "ClaimFailureCode",
    "ClaimState",
    "ClaimStatus",
    "ClaimTerminalStatus",
    "ClaimTransition",
    "ClaimedRun",
    "CommittedRunReplay",
    "CommittedTurnReceipt",
    "CompleteSettlement",
    "FailClaim",
    "FailedRunReceipt",
    "HeartbeatClaim",
    "InvalidClaimTransitionError",
    "RevokeClaim",
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
    "decide_claim_transition",
    "failure_code_for_terminal_status",
]
