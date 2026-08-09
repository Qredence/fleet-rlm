"""Internal claim and idempotency decisions for Run state facades.

The repository facades own state containers, rows, and transactions. These
pure decisions group the concurrency-sensitive rules that decide replay,
active-Run conflicts, and fresh claim construction in one place.
"""

from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from fleet_rlm.chat.run_lifecycle import (
    RunClaim,
    RunIdempotencyMismatchError,
    RunInProgressError,
    RunStateError,
    _RunClaimToken,
)


class _PriorRunView(Protocol):
    @property
    def input_fingerprint(self) -> str: ...

    @property
    def status(self) -> str: ...


def _prior_run_needs_replay(prior: _PriorRunView | None, request: RunClaim) -> bool:
    """Validate one prior idempotency match and return whether it replays."""
    if prior is None:
        return False
    fingerprint = prior.input_fingerprint
    if fingerprint not in request.input.acceptable_fingerprints:
        raise RunIdempotencyMismatchError("idempotency key is bound to different input")
    status = prior.status
    if status in {"running", "settling"}:
        raise RunInProgressError("Turn is already running")
    return status == "completed"


def _reject_active_run(active_run_exists: bool) -> None:
    """Enforce one active Run per Session for both facades."""
    if active_run_exists:
        raise RunInProgressError("Session already has a running Turn")


def _new_run_claim(base_checkpoint_version: int) -> _RunClaimToken:
    """Create a fresh claim token without mutating repository state."""
    return _RunClaimToken(uuid4(), base_checkpoint_version)


def _validate_completed_replay_state(*, committed: object | None, checkpoint_version: int | None) -> None:
    """Require the in-memory completed claim to carry its durable replay facts."""
    if committed is None or checkpoint_version is None:
        raise RunStateError("completed Run has no committed Turn")


def _claim_client_id(claim: _RunClaimToken) -> str:
    """Serialize the facade claim for persisted SQL ownership checks."""
    return str(claim.value)


def _claim_owner_matches(owner: str | None, claim: _RunClaimToken) -> bool:
    """Return true when a SQL row belongs to exactly this claim."""
    return owner == _claim_client_id(claim)


def _claim_snapshot_matches(base_checkpoint_version: int, claim: _RunClaimToken) -> bool:
    """Return true when the SQL row stayed on the claim's base Checkpoint."""
    return base_checkpoint_version == claim.base_checkpoint_version


def _validate_sql_claim(
    *,
    status: str,
    claim_owner: str | None,
    base_checkpoint_version: int,
    session_checkpoint_version: int,
    claim: _RunClaimToken,
) -> None:
    """Apply the complete SQL commit fencing decision in one place."""
    if (
        status != "running"
        or not _claim_owner_matches(claim_owner, claim)
        or not _claim_snapshot_matches(base_checkpoint_version, claim)
        or session_checkpoint_version != claim.base_checkpoint_version
    ):
        raise RunStateError("Turn claim or Checkpoint is stale")


__all__ = [
    "_claim_owner_matches",
    "_new_run_claim",
    "_prior_run_needs_replay",
    "_reject_active_run",
    "_validate_completed_replay_state",
    "_validate_sql_claim",
]
