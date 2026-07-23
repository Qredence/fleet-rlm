"""Pure Turn Claim transition policy shared by persistence adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, assert_never, cast

ClaimAction = Literal["fail", "settle", "revoke", "complete"]
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


class InvalidClaimTransition(ValueError):
    """The requested action is invalid for the current durable state."""


def transition_claim(
    action: ClaimAction,
    state: ClaimState,
    failure: ClaimFailure | None = None,
) -> ClaimTransition:
    """Return the durable state/receipt decision without performing I/O."""
    match action:
        case "fail":
            return _fail(state, _require_failure(failure))
        case "settle":
            return _settle(state, _require_failure(failure))
        case "revoke":
            return _revoke(state, _require_failure(failure))
        case "complete":
            return _complete(state)
        case _:
            assert_never(action)


def heartbeat_allowed(state: ClaimState) -> bool:
    return state.status in {"running", "settling"}


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


def _require_failure(failure: ClaimFailure | None) -> ClaimFailure:
    if failure is None:
        raise InvalidClaimTransition("transition requires a failure intent")
    return failure


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
