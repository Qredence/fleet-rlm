"""Pure Run claim transition policy shared by persistence adapters."""

from __future__ import annotations

from fleet_rlm.sessions.run_state import (
    BeginSettlement,
    ClaimCommand,
    ClaimDecision,
    ClaimFailure,
    ClaimFailureCode,
    ClaimState,
    ClaimStatus,
    ClaimTerminalStatus,
    ClaimTransition,
    CompleteSettlement,
    FailClaim,
    HeartbeatClaim,
    InvalidClaimTransitionError,
    RevokeClaim,
    decide_claim_transition,
    failure_code_for_terminal_status,
)

__all__ = [
    "BeginSettlement",
    "ClaimCommand",
    "ClaimDecision",
    "ClaimFailure",
    "ClaimFailureCode",
    "ClaimState",
    "ClaimStatus",
    "ClaimTerminalStatus",
    "ClaimTransition",
    "CompleteSettlement",
    "FailClaim",
    "HeartbeatClaim",
    "InvalidClaimTransitionError",
    "RevokeClaim",
    "decide_claim_transition",
    "failure_code_for_terminal_status",
]
