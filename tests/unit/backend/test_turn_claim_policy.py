"""Shared Turn Claim transition-policy coverage."""

from __future__ import annotations

import pytest

from fleet_rlm.chat.turn_claim import (
    ClaimFailure,
    ClaimState,
    InvalidClaimTransition,
    heartbeat_allowed,
    transition_claim,
)

FAILURE = ClaimFailure("failed", "execution_failed", "Turn failed")
SETTLING = ClaimState("settling", "execution_failed", FAILURE)


@pytest.mark.parametrize(
    ("action", "state", "status", "finalized"),
    (
        ("fail", ClaimState("running"), "failed", True),
        ("settle", ClaimState("running"), "failed", False),
        ("revoke", ClaimState("running"), "failed", False),
        ("complete", ClaimState("settling", "execution_failed", FAILURE), "failed", True),
    ),
)
def test_transition_claim_applies_one_policy(action, state, status, finalized) -> None:
    decision = transition_claim(action, state, None if action == "complete" else FAILURE)

    assert decision.status == status
    assert decision.finalized is finalized
    assert decision.next_state is not None


def test_revoke_policy_owns_stale_claim_terminal_intent() -> None:
    decision = transition_claim("revoke", ClaimState("running"), ClaimFailure("timeout", "timeout", "Timed out"))

    assert decision.next_state == ClaimState(
        "settling",
        "stale_claim",
        ClaimFailure("failed", "stale_claim", "Timed out"),
    )


@pytest.mark.parametrize("action", ["fail", "settle", "revoke"])
def test_completed_claim_rejects_failure_transitions(action) -> None:
    with pytest.raises(InvalidClaimTransition):
        transition_claim(action, ClaimState("completed"), FAILURE)


def test_terminal_transition_is_idempotent() -> None:
    decision = transition_claim("fail", ClaimState("failed", "execution_failed"), FAILURE)

    assert decision.finalized is True
    assert decision.next_state is None


def test_heartbeat_policy_only_accepts_owned_work_states() -> None:
    assert heartbeat_allowed(ClaimState("running"))
    assert heartbeat_allowed(ClaimState("settling"))
    assert not heartbeat_allowed(ClaimState("failed"))


@pytest.mark.parametrize(
    ("action", "state", "failure", "expected_status", "expected_finalized"),
    (
        ("fail", ClaimState("running"), FAILURE, "failed", True),
        ("fail", SETTLING, FAILURE, "failed", False),
        ("fail", ClaimState("failed", "execution_failed"), FAILURE, "failed", True),
        ("settle", ClaimState("running"), FAILURE, "failed", False),
        ("settle", SETTLING, FAILURE, "failed", False),
        ("settle", ClaimState("timeout", "timeout"), FAILURE, "timeout", True),
        ("revoke", ClaimState("running"), FAILURE, "failed", False),
        ("revoke", SETTLING, FAILURE, "failed", False),
        ("revoke", ClaimState("failed", "stale_claim"), FAILURE, "failed", True),
        ("complete", SETTLING, None, "failed", True),
        ("complete", ClaimState("failed", "stale_claim"), None, "failed", True),
    ),
)
def test_transition_claim_legal_matrix(action, state, failure, expected_status, expected_finalized) -> None:
    decision = transition_claim(action, state, failure)

    assert decision.status == expected_status
    assert decision.finalized is expected_finalized


@pytest.mark.parametrize(
    ("action", "state", "failure"),
    (
        ("fail", ClaimState("completed"), FAILURE),
        ("settle", ClaimState("completed"), FAILURE),
        ("revoke", ClaimState("completed"), FAILURE),
        ("revoke", ClaimState("cancelled", "cancelled"), FAILURE),
        ("complete", ClaimState("running"), None),
        ("complete", ClaimState("settling"), None),
    ),
)
def test_transition_claim_illegal_matrix(action, state, failure) -> None:
    with pytest.raises(InvalidClaimTransition):
        transition_claim(action, state, failure)
