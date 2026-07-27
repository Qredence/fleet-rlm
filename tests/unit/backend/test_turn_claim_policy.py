"""Shared Turn Claim transition-policy coverage."""

from __future__ import annotations

import pytest

from fleet_rlm.chat.turn_claim import (
    BeginSettlement,
    ClaimFailure,
    ClaimState,
    CompleteSettlement,
    FailClaim,
    HeartbeatClaim,
    InvalidClaimTransitionError,
    RevokeClaim,
    decide_claim_transition,
)

FAILURE = ClaimFailure("failed", "execution_failed", "Turn failed")
SETTLING = ClaimState("settling", "execution_failed", FAILURE)


@pytest.mark.parametrize(
    ("command", "state", "status", "finalized"),
    (
        (FailClaim(FAILURE), ClaimState("running"), "failed", True),
        (BeginSettlement(FAILURE), ClaimState("running"), "failed", False),
        (RevokeClaim(FAILURE), ClaimState("running"), "failed", False),
        (CompleteSettlement(), ClaimState("settling", "execution_failed", FAILURE), "failed", True),
    ),
)
def test_decide_claim_transition_applies_one_policy(command, state, status, finalized) -> None:
    decision = decide_claim_transition(state, command).transition

    assert decision is not None
    assert decision.status == status
    assert decision.finalized is finalized
    assert decision.next_state is not None


def test_revoke_policy_owns_stale_claim_terminal_intent() -> None:
    decision = decide_claim_transition(
        ClaimState("running"),
        RevokeClaim(ClaimFailure("timeout", "timeout", "Timed out")),
    ).transition

    assert decision is not None
    assert decision.next_state == ClaimState(
        "settling",
        "stale_claim",
        ClaimFailure("failed", "stale_claim", "Timed out"),
    )


@pytest.mark.parametrize("command", [FailClaim(FAILURE), BeginSettlement(FAILURE), RevokeClaim(FAILURE)])
def test_completed_claim_rejects_failure_transitions(command) -> None:
    with pytest.raises(InvalidClaimTransitionError):
        decide_claim_transition(ClaimState("completed"), command)


def test_terminal_transition_is_idempotent() -> None:
    decision = decide_claim_transition(ClaimState("failed", "execution_failed"), FailClaim(FAILURE)).transition

    assert decision is not None
    assert decision.finalized is True
    assert decision.next_state is None


def test_heartbeat_policy_only_accepts_owned_work_states() -> None:
    assert decide_claim_transition(ClaimState("running"), HeartbeatClaim()).heartbeat_allowed
    assert decide_claim_transition(ClaimState("settling"), HeartbeatClaim()).heartbeat_allowed
    assert not decide_claim_transition(ClaimState("failed"), HeartbeatClaim()).heartbeat_allowed


@pytest.mark.parametrize(
    ("command", "state", "expected_status", "expected_finalized"),
    (
        (FailClaim(FAILURE), ClaimState("running"), "failed", True),
        (FailClaim(FAILURE), SETTLING, "failed", False),
        (FailClaim(FAILURE), ClaimState("failed", "execution_failed"), "failed", True),
        (BeginSettlement(FAILURE), ClaimState("running"), "failed", False),
        (BeginSettlement(FAILURE), SETTLING, "failed", False),
        (BeginSettlement(FAILURE), ClaimState("timeout", "timeout"), "timeout", True),
        (RevokeClaim(FAILURE), ClaimState("running"), "failed", False),
        (RevokeClaim(FAILURE), SETTLING, "failed", False),
        (RevokeClaim(FAILURE), ClaimState("failed", "stale_claim"), "failed", True),
        (CompleteSettlement(), SETTLING, "failed", True),
        (CompleteSettlement(), ClaimState("failed", "stale_claim"), "failed", True),
    ),
)
def test_decide_claim_transition_legal_matrix(command, state, expected_status, expected_finalized) -> None:
    decision = decide_claim_transition(state, command).transition

    assert decision is not None
    assert decision.status == expected_status
    assert decision.finalized is expected_finalized


@pytest.mark.parametrize(
    ("command", "state"),
    (
        (FailClaim(FAILURE), ClaimState("completed")),
        (BeginSettlement(FAILURE), ClaimState("completed")),
        (RevokeClaim(FAILURE), ClaimState("completed")),
        (RevokeClaim(FAILURE), ClaimState("cancelled", "cancelled")),
        (CompleteSettlement(), ClaimState("running")),
        (CompleteSettlement(), ClaimState("settling")),
    ),
)
def test_decide_claim_transition_illegal_matrix(command, state) -> None:
    with pytest.raises(InvalidClaimTransitionError):
        decide_claim_transition(state, command)
