"""Pure Turn Claim transition policy shared by persistence adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, TypeAlias, assert_never, cast

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
    usage: Mapping[str, object] = field(default_factory=dict)  # adapter durability metadata; ignored by policy


@dataclass(frozen=True, slots=True)
class BeginSettlement:
    failure: ClaimFailure
    usage: Mapping[str, object] = field(default_factory=dict)  # adapter durability metadata; ignored by policy


@dataclass(frozen=True, slots=True)
class RevokeClaim:
    failure: ClaimFailure
    usage: Mapping[str, object] = field(default_factory=dict)  # adapter durability metadata; ignored by policy


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


class InvalidClaimTransition(ValueError):
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
        raise InvalidClaimTransition("a committed Run cannot be failed")
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
        raise InvalidClaimTransition("a committed Run cannot be settled")
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
        raise InvalidClaimTransition("a committed Run cannot be revoked")
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
        raise InvalidClaimTransition("a terminal Run cannot be revoked")
    return ClaimTransition("failed", "stale_claim", (state.intent or failure).public_message, False)


def _complete(state: ClaimState) -> ClaimTransition:
    if state.status == "failed" and state.failure_code == "stale_claim":
        return ClaimTransition("failed", "stale_claim", "Turn failed", True)
    if state.status != "settling" or state.intent is None:
        raise InvalidClaimTransition("Turn is not settling under this claim")
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
        raise InvalidClaimTransition("persisted Run has an invalid failure status")
    return cast(ClaimTerminalStatus, status)


def _failure_code(state: ClaimState) -> ClaimFailureCode:
    if state.failure_code is not None:
        return state.failure_code
    codes: dict[ClaimTerminalStatus, ClaimFailureCode] = {
        "failed": "execution_failed",
        "cancelled": "cancelled",
        "timeout": "timeout",
    }
    return codes[_terminal_status(state.status)]
