"""Internal liveness, fencing, and cancellation operations for Run persistence.

Every function receives a facade-owned SQLAlchemy `AsyncSession`; callers own
session scope and transaction scope. No helper probes the database through an
independent transaction.
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fleet_rlm.chat.run_claim import (
    ClaimFailure,
    CompleteSettlement,
    InvalidClaimTransitionError,
    RevokeClaim,
    decide_claim_transition,
)
from fleet_rlm.chat.run_lifecycle import CancelResult, RunNotFoundError
from fleet_rlm.persistence.models import RunRow, SessionRow, TurnRow
from fleet_rlm.persistence.repositories.run_codec import _apply_row_next_state, _cancel_tombstone_rows, _row_claim_state
from fleet_rlm.sessions.models import TurnAccess, TurnInput


async def _await_recovery_step(awaitable: Awaitable[Any], *, deadline: float | None) -> Any:
    """Await one recovery operation without exceeding the shared startup deadline."""
    if deadline is None:
        return await awaitable
    import asyncio

    async with asyncio.timeout_at(deadline):
        return await awaitable


def _recovery_deadline_exhausted(deadline: float | None) -> bool:
    """Determine whether the recovery deadline has been reached."""
    if deadline is None:
        return False
    import asyncio

    return asyncio.get_running_loop().time() >= deadline


async def _mark_cancel_requested(db: AsyncSession, access: TurnAccess, run_id: UUID) -> CancelResult:
    """Apply the cancellation-mark state machine inside the facade transaction."""
    run = await db.scalar(
        select(RunRow)
        .join(SessionRow, SessionRow.id == RunRow.session_id)
        .where(
            RunRow.id == run_id,
            SessionRow.user_id == access.user_id,
            SessionRow.workspace_id == access.workspace_id,
        )
        .with_for_update()
    )
    if run is None:
        raise RunNotFoundError("Turn not found")
    if run.status != "running":
        return "already_terminal"
    if run.cancel_requested_at is not None:
        return "already_requested"
    run.cancel_requested_at = datetime.now(UTC)
    return "requested"


async def _load_recovery_candidates(db: AsyncSession, *, batch_size: int) -> list[RunRow]:
    """Load bounded nonterminal claims that require startup ownership recovery."""
    return list(
        (
            await db.scalars(
                select(RunRow)
                .where(
                    RunRow.status.in_(("running", "settling")),
                    RunRow.claim_owner.is_not(None),
                    RunRow.claim_heartbeat_at.is_not(None),
                )
                .order_by(RunRow.claim_heartbeat_at, RunRow.created_at)
                .limit(batch_size)
            )
        ).all()
    )


async def _claim_recovery_owner(db: AsyncSession, pending_run: RunRow) -> str | None:
    """Take conditional recovery ownership inside the facade transaction."""
    owner = pending_run.claim_owner
    heartbeat = pending_run.claim_heartbeat_at
    if owner is None or heartbeat is None:
        return None
    recovery_owner = f"recovery:{uuid4()}"
    await db.execute(
        update(RunRow)
        .where(
            RunRow.id == pending_run.id,
            RunRow.status == pending_run.status,
            RunRow.claim_owner == owner,
            RunRow.claim_heartbeat_at == heartbeat,
        )
        .values(claim_owner=recovery_owner)
    )
    claimed_id = await db.scalar(
        select(RunRow.id).where(
            RunRow.id == pending_run.id,
            RunRow.claim_owner == recovery_owner,
        )
    )
    return recovery_owner if claimed_id is not None else None


async def _restore_after_fence_failure(
    db: AsyncSession,
    pending_run: RunRow,
    recovery_owner: str,
    *,
    original_owner: str | None,
) -> None:
    """Restore the prior owner and record retry metadata after a fence failure."""
    run = await db.get(RunRow, pending_run.id, with_for_update=True)
    if run is None or run.status != pending_run.status or run.claim_owner != recovery_owner:
        return
    metadata = dict(run.recovery_metadata_json or {})
    recovery = dict(metadata.get("recovery") or {})
    prior_attempts = recovery.get("attempts", 0)
    if not isinstance(prior_attempts, int) or isinstance(prior_attempts, bool):
        prior_attempts = 0
    recovery["attempts"] = max(0, prior_attempts) + 1
    recovery["last_error"] = "provider_fence_failed"
    metadata["recovery"] = recovery
    run.recovery_metadata_json = metadata
    run.claim_owner = original_owner


async def _complete_recovery(db: AsyncSession, pending_run: RunRow, recovery_owner: str) -> bool:
    """Apply the stale-claim transition and release recovery ownership atomically."""
    run = await db.get(RunRow, pending_run.id, with_for_update=True)
    if run is None or run.status != pending_run.status or run.claim_owner != recovery_owner:
        return False
    if run.status == "running":
        stale = ClaimFailure("failed", "stale_claim", "Turn failed")
        revocation = decide_claim_transition(_row_claim_state(run), RevokeClaim(stale)).transition
        if revocation is None or revocation.next_state is None:
            return False
        _apply_row_next_state(
            run,
            revocation.next_state,
            public_message=revocation.public_message,
        )
        decision = decide_claim_transition(revocation.next_state, CompleteSettlement()).transition
    else:
        try:
            decision = decide_claim_transition(_row_claim_state(run), CompleteSettlement()).transition
        except InvalidClaimTransitionError:
            return False
    if decision is None or decision.next_state is None:
        return False
    _apply_row_next_state(
        run,
        decision.next_state,
        public_message=decision.public_message,
    )
    run.finished_at = datetime.now(UTC)
    run.claim_owner = None
    run.claim_heartbeat_at = None
    run.recovery_metadata_json = None
    if decision.next_state.status == "cancelled":
        last_sequence = int(
            await db.scalar(
                select(func.coalesce(func.max(TurnRow.sequence), 0)).where(TurnRow.session_id == run.session_id)
            )
            or 0
        )
        db.add_all(_cancel_tombstone_rows(run=run, turn_input=None, next_sequence=last_sequence + 1))
    return True


def _heartbeat_unavailable(*, heartbeat_allowed: bool, claim_owned: bool) -> bool:
    """Group heartbeat fencing so a disallowed/foreign claim never touches rows."""
    return not heartbeat_allowed or not claim_owned


def _touch_claim_heartbeat(run: RunRow) -> None:
    """Refresh liveness for one correctly fenced claim."""
    run.claim_heartbeat_at = datetime.now(UTC)


async def _persist_cancel_tombstone(
    db: AsyncSession,
    run: RunRow,
    turn_input: TurnInput | None,
) -> None:
    """Persist the cancelled terminal tombstone using the shared codec."""
    last_sequence = int(
        await db.scalar(
            select(func.coalesce(func.max(TurnRow.sequence), 0)).where(TurnRow.session_id == run.session_id)
        )
        or 0
    )
    db.add_all(_cancel_tombstone_rows(run=run, turn_input=turn_input, next_sequence=last_sequence + 1))
