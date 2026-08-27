"""Internal final-state transitions for the deep Run persistence facade.

Facades own locks, AsyncSessions, and transactions. These helpers receive the
already loaded state or facade-owned session and apply commit/settlement
shapes without changing the durable invariants or public lifecycle contract.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fleet_rlm.artifacts.promotion import PromotedArtifact
from fleet_rlm.chat.run_claim import (
    BeginSettlement,
    ClaimCommand,
    CompleteSettlement,
    FailClaim,
    HeartbeatClaim,
    InvalidClaimTransitionError,
    RevokeClaim,
    decide_claim_transition,
)
from fleet_rlm.chat.run_lifecycle import (
    ClaimedRun,
    CommittedTurnReceipt,
    FailedRunReceipt,
    RunAlreadyCompletedError,
    RunNotFoundError,
    RunStateError,
)
from fleet_rlm.files.memory_candidates import MemoryPromotionIntent
from fleet_rlm.persistence.models import MemoryPromotionIntentRow, RunRow, SessionRow, TurnRow
from fleet_rlm.persistence.repositories.run_claim_decisions import _claim_owner_matches, _validate_sql_claim
from fleet_rlm.persistence.repositories.run_codec import (
    _apply_memory_next_state,
    _apply_row_next_state,
    _artifact_row_for_commit,
    _command_usage,
    _committed_turn_rows,
    _memory_claim_state,
    _row_claim_state,
    _transition_receipt,
)
from fleet_rlm.persistence.repositories.run_liveness import (
    _heartbeat_unavailable,
    _persist_cancel_tombstone,
    _touch_claim_heartbeat,
)
from fleet_rlm.sessions.models import HistoryMessage


async def _authorized_sql_session(db: AsyncSession, run: ClaimedRun, row: RunRow) -> SessionRow:
    """Load the Run's Session and enforce tenant/workspace ownership.

    A caller can present a stale or forged ``ClaimedRun`` even when the Run
    is already terminal. Authorization therefore happens before both replay
    and every state transition, not only before mutable claim validation.
    """
    if row.session_id != run.session_id:
        raise RunNotFoundError("Turn not found")
    session = await db.scalar(
        select(SessionRow)
        .where(
            SessionRow.id == row.session_id,
            SessionRow.user_id == run.access.user_id,
            SessionRow.workspace_id == run.access.workspace_id,
        )
        .with_for_update()
    )
    if session is None:
        raise RunNotFoundError("Turn not found")
    return session


def _commit_memory_run(
    state,
    session,
    run: ClaimedRun,
    committed,
    artifacts: tuple[PromotedArtifact, ...],
) -> CommittedTurnReceipt:
    """Apply one successful in-memory commit to facade-owned immutable slots."""
    session.history.extend(
        (
            HistoryMessage("user", run.input.text),
            HistoryMessage("assistant", committed.text, committed),
        )
    )
    session.checkpoint_version += 1
    state.status = "completed"
    state.user_turn_id = uuid4()
    state.record_sequence = session.turn_sequence + 1
    session.turn_sequence += 2
    state.committed = committed
    state.checkpoint_version = session.checkpoint_version
    refs = tuple(item.ref for item in artifacts)
    state.artifacts = refs
    return CommittedTurnReceipt(state.run_id, session.checkpoint_version, committed, refs)


def _transition_memory_claim(
    state,
    run: ClaimedRun,
    command: ClaimCommand,
    *,
    persist_cancel_tombstone: Callable[..., None],
) -> FailedRunReceipt | None:
    """Apply one in-memory final-state command under the facade lock."""
    stale_terminal = (
        isinstance(command, CompleteSettlement) and state.status == "failed" and state.failure_code == "stale_claim"
    )
    if not isinstance(command, RevokeClaim) and not stale_terminal and state.claim != run._claim:
        raise RunStateError("Turn claim is invalid")
    try:
        decision = decide_claim_transition(_memory_claim_state(state), command)
    except InvalidClaimTransitionError as exc:
        raise RunStateError(str(exc)) from exc
    if isinstance(command, HeartbeatClaim):
        if not decision.heartbeat_allowed:
            raise RunStateError("Turn claim is invalid")
        return None
    transition = decision.transition
    if transition is None:
        raise RunStateError("claim decision did not include a transition")
    if transition.next_state is not None:
        _apply_memory_next_state(state, transition.next_state, usage=_command_usage(command))
        if transition.next_state.status == "cancelled":
            persist_cancel_tombstone(state, usage=_command_usage(command))
    return _transition_receipt(state.run_id, transition)


async def _commit_sql_run(
    db: AsyncSession,
    run: ClaimedRun,
    committed,
    artifacts: tuple[PromotedArtifact, ...],
    memory_intents: tuple[MemoryPromotionIntent, ...] = (),
) -> CommittedTurnReceipt:
    """Apply the successful SQL commit inside the facade-owned transaction."""
    row = await db.get(RunRow, run.run_id, with_for_update=True)
    if row is None:
        raise RunNotFoundError("Turn not found")
    session = await _authorized_sql_session(db, run, row)
    if row.status == "completed":
        from fleet_rlm.persistence.repositories.run_queries import _committed_receipt

        return await _committed_receipt(db, row)
    _validate_sql_claim(
        status=row.status,
        claim_owner=row.claim_owner,
        base_checkpoint_version=row.base_checkpoint_version,
        session_checkpoint_version=session.checkpoint_version,
        claim=run._claim,
    )
    last_sequence = int(
        await db.scalar(
            select(func.coalesce(func.max(TurnRow.sequence), 0)).where(TurnRow.session_id == run.session_id)
        )
        or 0
    )
    db.add_all(
        _committed_turn_rows(
            run_id=run.run_id,
            session_id=run.session_id,
            run_input=run.input,
            committed=committed,
            first_sequence=last_sequence + 1,
        )
    )
    db.add_all(_artifact_row_for_commit(run, item) for item in artifacts)
    # P23/QRE-165: crash-recoverable Memory promotion intents ride the same
    # transaction as the successful Turn commit; a rollback here (including
    # the revocation recheck below) eliminates intents with everything else.
    db.add_all(_memory_intent_row_for_commit(run, intent) for intent in memory_intents)
    session.checkpoint_version += 1
    row.status = "completed"
    row.commit_checkpoint_version = session.checkpoint_version
    row.finished_at = datetime.now(UTC)
    row.claim_owner = None
    row.claim_heartbeat_at = None
    if run.authority.revoked:
        raise RunStateError("Turn claim is invalid")
    return CommittedTurnReceipt(
        run.run_id,
        session.checkpoint_version,
        committed,
        tuple(item.ref for item in artifacts),
    )


def _memory_intent_row_for_commit(run: ClaimedRun, intent: MemoryPromotionIntent) -> MemoryPromotionIntentRow:
    """Build the pinned outbox row for one candidate inside the commit tx."""
    return MemoryPromotionIntentRow(
        run_id=run.run_id,
        session_id=run.session_id,
        workspace_id=run.access.workspace_id,
        user_id=run.access.user_id,
        candidate_ordinal=intent.candidate_ordinal,
        candidate_id=intent.candidate_id,
        category=intent.category,
        learning=intent.learning,
        byte_size=intent.byte_size,
        supersedes_id=intent.supersedes_id,
        memory_id=intent.memory_id,
        record_text=intent.record_text,
        source=intent.source,
        status="pending",
    )


async def _transition_sql_claim(db: AsyncSession, run: ClaimedRun, command: ClaimCommand) -> FailedRunReceipt | None:
    """Apply one SQL final-state command inside the facade-owned transaction."""
    row = await db.get(RunRow, run.run_id, with_for_update=True)
    if row is None:
        raise RunNotFoundError("Turn not found")
    await _authorized_sql_session(db, run, row)
    if row.status == "completed":
        raise RunAlreadyCompletedError("Turn already committed")
    stale_terminal = (
        isinstance(command, CompleteSettlement) and row.status == "failed" and row.failure_code == "stale_claim"
    )
    if (
        not isinstance(command, RevokeClaim)
        and not stale_terminal
        and not _claim_owner_matches(row.claim_owner, run._claim)
    ):
        raise RunStateError("Turn claim is invalid")
    try:
        decision = decide_claim_transition(_row_claim_state(row), command)
    except InvalidClaimTransitionError as exc:
        raise RunStateError(str(exc)) from exc
    if isinstance(command, HeartbeatClaim):
        if _heartbeat_unavailable(
            heartbeat_allowed=decision.heartbeat_allowed,
            claim_owned=_claim_owner_matches(row.claim_owner, run._claim),
        ):
            raise RunStateError("Turn claim is invalid")
        _touch_claim_heartbeat(row)
        return None
    transition = decision.transition
    if transition is None:
        raise RunStateError("claim decision did not include a transition")
    if transition.next_state is not None:
        _apply_row_next_state(
            row,
            transition.next_state,
            public_message=transition.public_message,
            usage=_command_usage(command),
        )
        if isinstance(command, (FailClaim, CompleteSettlement)):
            # The cancelled terminal shape (claim released) must flush before
            # the tombstone's sequence read autoflushes the row.
            row.finished_at = datetime.now(UTC)
            row.claim_owner = None
            row.claim_heartbeat_at = None
        elif isinstance(command, (BeginSettlement, RevokeClaim)):
            row.recovery_metadata_json = {"cleanup": "pending"}
        if transition.next_state.status == "cancelled":
            await _persist_cancel_tombstone(db, row, run.input)
    return _transition_receipt(row.id, transition)
