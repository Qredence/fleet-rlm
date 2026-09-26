"""Unified Turn lifecycle state persistence adapter (in-memory and SQLAlchemy).

Consolidates claim decisions, ORM/JSON codecs, queries, liveness/recovery, and
final-state commit/settlement handlers into a single authoritative Turn domain.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.artifacts.models import ArtifactRef
from fleet_rlm.artifacts.promotion import PromotedArtifact
from fleet_rlm.artifacts.safety import parse_kind
from fleet_rlm.persistence.database import DatabaseConnectionError, observe_database_operation
from fleet_rlm.persistence.models import (
    ArtifactRow,
    MemoryPromotionIntentRow,
    RunRow,
    SessionRow,
    TurnRow,
)
from fleet_rlm.sessions.committed_turn import (
    CommittedTurn,
    CommittedTurnCodec,
    commit_cancelled_tombstone,
)
from fleet_rlm.sessions.models import (
    AssistantTurnRecord,
    HistoryMessage,
    SessionHistory,
    TurnAccess,
    TurnInput,
    TurnInputCodec,
    UserTurnRecord,
)
from fleet_rlm.sessions.run_claim import (
    BeginSettlement,
    ClaimCommand,
    ClaimFailure,
    ClaimFailureCode,
    ClaimState,
    ClaimStatus,
    ClaimTransition,
    CompleteSettlement,
    FailClaim,
    HeartbeatClaim,
    InvalidClaimTransitionError,
    RevokeClaim,
    decide_claim_transition,
    failure_code_for_terminal_status,
)
from fleet_rlm.sessions.run_state import (
    CancelResult,
    ClaimedRun,
    CommittedRunReplay,
    CommittedTurnReceipt,
    FailedRunReceipt,
    RunAlreadyCompletedError,
    RunAuthority,
    RunClaim,
    RunFailure,
    RunFailureCode,
    RunIdempotencyMismatchError,
    RunInProgressError,
    RunLifecycleUnavailableError,
    RunNotFoundError,
    RunStart,
    RunStateError,
    _RunClaimToken,
)
from fleet_rlm.sessions.usage import RLMUsage, empty_rlm_usage
from fleet_rlm.workspace.memory import MemoryPromotionIntent

# ---------------------------------------------------------------------------
# In-memory domain state models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _RunState:
    run_id: UUID
    session_id: UUID
    access: TurnAccess
    idempotency_key: str
    input_fingerprint: str
    input: TurnInput
    claim: _RunClaimToken
    status: Literal["running", "settling", "completed", "failed", "cancelled", "timeout"]
    authority: RunAuthority = field(default_factory=RunAuthority)
    failure_code: RunFailureCode | None = None
    terminal_intent: RunFailure | None = None
    cancel_requested: bool = False
    committed: CommittedTurn | None = None
    checkpoint_version: int | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    user_turn_id: UUID | None = None
    tombstone: CommittedTurn | None = None
    record_sequence: int | None = None
    recovery_attempts: int = 0
    recovery_last_error: str | None = None


@dataclass(slots=True)
class _SessionState:
    access: TurnAccess
    history: list[HistoryMessage] = field(default_factory=list)
    checkpoint_version: int = 0
    turn_sequence: int = 0
    status: Literal["active", "archived"] = "active"


@dataclass(frozen=True, slots=True)
class ReconciliationSummary:
    """Bounded startup-recovery accounting returned by Turn state adapters."""

    candidates: int = 0
    recovered: int = 0
    fence_failures: int = 0
    skipped: int = 0
    budget_exhausted: bool = False


# ---------------------------------------------------------------------------
# Claim & Idempotency Decisions
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Codec & Value Converters
# ---------------------------------------------------------------------------


def _decode_failure_status(value: str) -> Literal["failed", "cancelled", "timeout"]:
    """Validate and return a persisted Run failure status."""
    if value in {"failed", "cancelled", "timeout"}:
        return value
    raise RunStateError("persisted Run has an invalid failure status")


def _decode_failure_code(
    value: str | None,
    *,
    status: Literal["failed", "cancelled", "timeout"],
) -> RunFailureCode:
    if value in {"preparation_failed", "execution_failed", "commit_failed", "cancelled", "timeout", "stale_claim"}:
        return value
    if value in {None, "failed"}:
        return failure_code_for_terminal_status(status)
    raise RunStateError("persisted Run has an invalid failure code")


def _decode_claim_status(value: str) -> ClaimStatus:
    if value in {"running", "settling", "completed", "failed", "cancelled", "timeout"}:
        return value
    raise RunStateError("persisted Run has an invalid claim status")


def _decode_claim_code(value: str | None) -> ClaimFailureCode | None:
    if value is None:
        return None
    if value in {"preparation_failed", "execution_failed", "commit_failed", "cancelled", "timeout", "stale_claim"}:
        return value
    raise RunStateError("persisted Run has an invalid failure code")


def _claim_failure(failure: RunFailure) -> ClaimFailure:
    return ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message)


def _command_usage(command: ClaimCommand) -> RLMUsage | None:
    if hasattr(command, "usage"):
        return cast(RLMUsage, command.usage)
    return None


def _turn_failure(intent: ClaimFailure, usage: RLMUsage) -> RunFailure:
    return RunFailure(intent.status, intent.code, intent.public_message, usage)


def _memory_claim_state(run: Any) -> ClaimState:
    intent = _claim_failure(run.terminal_intent) if run.terminal_intent is not None else None
    return ClaimState(_decode_claim_status(run.status), _decode_claim_code(run.failure_code), intent)


def _row_claim_state(run: RunRow) -> ClaimState:
    intent = None
    if run.terminal_intent is not None:
        status = _decode_failure_status(run.terminal_intent)
        intent = ClaimFailure(
            status,
            _decode_failure_code(run.failure_code, status=status),
            run.failure_public_message or "Turn failed",
        )
    return ClaimState(_decode_claim_status(run.status), _decode_claim_code(run.failure_code), intent)


def _transition_receipt(run_id: UUID, decision: ClaimTransition) -> FailedRunReceipt:
    status = _decode_failure_status(decision.status)
    return FailedRunReceipt(
        run_id,
        status,
        _decode_failure_code(decision.failure_code, status=status),
        decision.public_message,
        decision.finalized,
    )


def _apply_memory_next_state(
    run: Any,
    next_state: ClaimState,
    *,
    usage: RLMUsage | None = None,
) -> None:
    run.status = cast(Any, next_state.status)
    run.failure_code = cast(RunFailureCode, next_state.failure_code)
    if next_state.intent is not None:
        if usage is None:
            raise RunStateError("claim intent application requires usage")
        run.terminal_intent = _turn_failure(next_state.intent, usage)


def _apply_row_next_state(
    run: RunRow,
    next_state: ClaimState,
    *,
    public_message: str,
    usage: RLMUsage | None = None,
) -> None:
    run.status = next_state.status
    run.failure_code = next_state.failure_code
    run.failure_public_message = public_message
    if usage is not None:
        run.failure_usage_json = dict(usage)
    if next_state.intent is not None:
        run.terminal_intent = next_state.intent.status


def _encode_turn_input(value: TurnInput) -> dict[str, Any]:
    return TurnInputCodec.encode(value)


def _decode_turn_input(value: object) -> TurnInput:
    return TurnInputCodec.decode(value)


def _encode_committed_turn(value: CommittedTurn) -> dict[str, Any]:
    return CommittedTurnCodec.encode(value)


def _decode_committed_turn(value: object) -> CommittedTurn:
    return CommittedTurnCodec.decode(value)


def _cancelled_tombstone(usage: RLMUsage) -> CommittedTurn:
    return commit_cancelled_tombstone(usage)


def _committed_turn_rows(
    *,
    run_id: UUID,
    session_id: UUID,
    run_input: TurnInput,
    committed: CommittedTurn,
    first_sequence: int,
) -> tuple[TurnRow, TurnRow]:
    return (
        TurnRow(
            id=uuid4(),
            session_id=session_id,
            run_id=run_id,
            sequence=first_sequence,
            role="user",
            user_input_json=_encode_turn_input(run_input),
            committed_turn_json=None,
        ),
        TurnRow(
            id=run_id,
            session_id=session_id,
            run_id=run_id,
            sequence=first_sequence + 1,
            role="assistant",
            user_input_json=None,
            committed_turn_json=_encode_committed_turn(committed),
        ),
    )


def _artifact_row_for_commit(run: ClaimedRun, artifact: PromotedArtifact) -> ArtifactRow:
    ref = artifact.ref
    return ArtifactRow(
        id=ref.id,
        user_id=run.access.user_id,
        workspace_id=run.access.workspace_id,
        session_id=ref.session_id,
        run_id=ref.run_id,
        kind=ref.kind,
        title=ref.title,
        media_type=ref.media_type,
        byte_size=ref.byte_size,
        checksum_sha256=ref.checksum_sha256,
        storage_ref=artifact.storage_ref,
    )


def _cancel_tombstone_rows(
    *,
    run: RunRow,
    turn_input: TurnInput | None,
    next_sequence: int,
) -> tuple[TurnRow, ...]:
    usage: RLMUsage = cast(RLMUsage, run.failure_usage_json) if run.failure_usage_json else empty_rlm_usage()
    rows: list[TurnRow] = []
    if turn_input is not None:
        rows.append(
            TurnRow(
                id=uuid4(),
                session_id=run.session_id,
                run_id=run.id,
                sequence=next_sequence,
                role="user",
                user_input_json=_encode_turn_input(turn_input),
                committed_turn_json=None,
            )
        )
        next_sequence += 1
    rows.append(
        TurnRow(
            id=run.id,
            session_id=run.session_id,
            run_id=run.id,
            sequence=next_sequence,
            role="assistant",
            user_input_json=None,
            committed_turn_json=_encode_committed_turn(_cancelled_tombstone(usage)),
        )
    )
    return tuple(rows)


def _artifact_ref_from_row(row: ArtifactRow) -> ArtifactRef:
    return ArtifactRef(
        row.id,
        row.session_id,
        row.run_id,
        parse_kind(row.kind),
        row.title,
        row.media_type,
        row.byte_size,
        row.checksum_sha256 or "",
    )


def _artifact_refs_from_rows(rows: Iterable[ArtifactRow]) -> tuple[ArtifactRef, ...]:
    return tuple(_artifact_ref_from_row(row) for row in rows)


def _history_from_turn_rows(rows: Sequence[TurnRow]) -> SessionHistory:
    """Project durable Turn rows into the Session History message snapshot."""
    messages: list[HistoryMessage] = []
    for row in rows:
        if row.role == "user" and row.user_input_json is not None:
            messages.append(HistoryMessage("user", _decode_turn_input(row.user_input_json).text))
        elif row.role == "assistant" and row.committed_turn_json is not None:
            committed = _decode_committed_turn(row.committed_turn_json)
            messages.append(HistoryMessage("assistant", committed.text, committed))
        else:
            raise RunStateError("stored Turn shape is invalid")
    return SessionHistory(tuple(messages))


# ---------------------------------------------------------------------------
# Internal Query Projections
# ---------------------------------------------------------------------------


async def _committed_output(db: AsyncSession, run: RunRow) -> tuple[CommittedTurn, int]:
    """Load validated committed output without fetching unrelated artifact rows."""
    row = await db.scalar(select(TurnRow).where(TurnRow.run_id == run.id, TurnRow.role == "assistant"))
    if row is None or row.committed_turn_json is None or run.commit_checkpoint_version is None:
        raise RunStateError("completed Run has no committed Turn")
    return _decode_committed_turn(row.committed_turn_json), run.commit_checkpoint_version


async def _committed_receipt(db: AsyncSession, run: RunRow) -> CommittedTurnReceipt:
    """Project one committed SQL row and its Artifacts to the domain receipt."""
    committed, checkpoint = await _committed_output(db, run)
    artifact_rows = (
        await db.scalars(select(ArtifactRow).where(ArtifactRow.run_id == run.id).order_by(ArtifactRow.created_at))
    ).all()
    return CommittedTurnReceipt(run.id, checkpoint, committed, _artifact_refs_from_rows(artifact_rows))


async def _committed_replay(db: AsyncSession, run: RunRow) -> CommittedRunReplay:
    """Project the durable replay shape for an existing committed Run."""
    committed, checkpoint = await _committed_output(db, run)
    return CommittedRunReplay(run.id, run.session_id, committed, checkpoint)


async def _session_history(db: AsyncSession, session_id: UUID) -> SessionHistory:
    """Project durable ordered Turn rows to the in-memory Session History view."""
    rows = (await db.scalars(select(TurnRow).where(TurnRow.session_id == session_id).order_by(TurnRow.sequence))).all()
    return _history_from_turn_rows(rows)


# ---------------------------------------------------------------------------
# Liveness, Fencing & Recovery Helpers
# ---------------------------------------------------------------------------


async def _await_recovery_step(awaitable: Awaitable[Any], *, deadline: float | None) -> Any:
    """Await one recovery operation without exceeding the shared startup deadline."""
    if deadline is None:
        return await awaitable
    async with asyncio.timeout_at(deadline):
        return await awaitable


def _recovery_deadline_exhausted(deadline: float | None) -> bool:
    """Determine whether the recovery deadline has been reached."""
    if deadline is None:
        return False
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


# ---------------------------------------------------------------------------
# Final State Commits & Transitions
# ---------------------------------------------------------------------------


async def _authorized_sql_session(db: AsyncSession, run: ClaimedRun, row: RunRow) -> SessionRow:
    """Load the Run's Session and enforce tenant/workspace ownership."""
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
    state: _RunState,
    session: _SessionState,
    run: ClaimedRun,
    committed: CommittedTurn,
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
    state: _RunState,
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


async def _serialize_sqlite_final_state(db: AsyncSession, run: ClaimedRun) -> None:
    """Acquire SQLite's writer lock before reading a mutable final-state row."""
    if db.get_bind().dialect.name == "sqlite":
        await db.execute(update(RunRow).where(RunRow.id == run.run_id).values(id=RunRow.id))


async def _commit_sql_run(
    db: AsyncSession,
    run: ClaimedRun,
    committed: CommittedTurn,
    artifacts: tuple[PromotedArtifact, ...],
    memory_intents: tuple[MemoryPromotionIntent, ...] = (),
) -> CommittedTurnReceipt:
    """Apply the successful SQL commit inside the facade-owned transaction."""
    await _serialize_sqlite_final_state(db, run)
    row = await db.get(RunRow, run.run_id, with_for_update=True)
    if row is None:
        raise RunNotFoundError("Turn not found")
    session = await _authorized_sql_session(db, run, row)
    if row.status == "completed":
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


async def _transition_sql_claim(db: AsyncSession, run: ClaimedRun, command: ClaimCommand) -> FailedRunReceipt | None:
    """Apply one SQL final-state command inside the facade-owned transaction."""
    await _serialize_sqlite_final_state(db, run)
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
            row.finished_at = datetime.now(UTC)
            row.claim_owner = None
            row.claim_heartbeat_at = None
        elif isinstance(command, (BeginSettlement, RevokeClaim)):
            row.recovery_metadata_json = {"cleanup": "pending"}
        if transition.next_state.status == "cancelled":
            await _persist_cancel_tombstone(db, row, run.input)
    return _transition_receipt(row.id, transition)


# ---------------------------------------------------------------------------
# Store Implementations
# ---------------------------------------------------------------------------


class InMemoryRunStateStore:
    """Lock-backed parity adapter for private composition and lifecycle tests."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: dict[UUID, _SessionState] = {}
        self._runs: dict[UUID, _RunState] = {}
        self._keys: dict[tuple[UUID, str], UUID] = {}
        self._recovery_runs: set[UUID] = set()

    async def add_session(
        self,
        session_id: UUID,
        access: TurnAccess,
        *,
        history: SessionHistory | None = None,
        checkpoint_version: int = 0,
        status: Literal["active", "archived"] = "active",
    ) -> None:
        async with self._lock:
            self._sessions[session_id] = _SessionState(
                access=access,
                history=list(history.messages) if history is not None else [],
                checkpoint_version=checkpoint_version,
                status=status,
            )

    async def set_session_status(
        self,
        session_id: UUID,
        access: TurnAccess,
        status: Literal["active", "archived"],
    ) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.access != access:
                raise RunNotFoundError("Turn not found")
            session.status = status

    async def begin(self, request: RunClaim) -> RunStart:
        async with self._lock:
            session = self._sessions.get(request.session_id)
            if session is None or session.access != request.access or session.status != "active":
                raise RunNotFoundError("Turn not found")
            key = (request.session_id, request.idempotency_key)
            prior_id = self._keys.get(key)
            prior = self._runs.get(prior_id) if prior_id is not None else None
            if _prior_run_needs_replay(prior, request):
                assert prior is not None
                _validate_completed_replay_state(committed=prior.committed, checkpoint_version=prior.checkpoint_version)
                assert prior.committed is not None and prior.checkpoint_version is not None
                return CommittedRunReplay(
                    prior.run_id,
                    prior.session_id,
                    prior.committed,
                    prior.checkpoint_version,
                )
            _reject_active_run(
                any(
                    run.session_id == request.session_id and run.status in {"running", "settling"}
                    for run in self._runs.values()
                )
            )

            claim = _new_run_claim(session.checkpoint_version)
            run = _RunState(
                request.proposed_run_id,
                request.session_id,
                request.access,
                request.idempotency_key,
                request.input.fingerprint,
                request.input,
                claim,
                "running",
            )
            self._runs[run.run_id] = run
            self._keys[key] = run.run_id

            async def cancelled() -> bool:
                async with self._lock:
                    current = self._runs.get(run.run_id)
                    return current is None or current.cancel_requested

            return ClaimedRun(
                run.run_id,
                run.session_id,
                run.access,
                request.input,
                SessionHistory(tuple(session.history)),
                cancelled,
                claim,
                run.authority,
            )

    async def commit(
        self,
        run: ClaimedRun,
        committed: CommittedTurn,
        artifacts: tuple[PromotedArtifact, ...],
        memory_intents: tuple[MemoryPromotionIntent, ...] = (),
    ) -> CommittedTurnReceipt:
        del memory_intents
        async with self._lock:
            if run.authority.revoked:
                raise RunStateError("Turn claim is invalid")
            state, session = self._claimed(run)
            return _commit_memory_run(state, session, run, committed, artifacts)

    def _persist_cancel_tombstone(self, run: _RunState, *, usage: RLMUsage | None = None) -> None:
        session = self._sessions.get(run.session_id)
        if session is None or run.tombstone is not None:
            return
        if usage is None:
            usage = run.terminal_intent.usage if run.terminal_intent is not None else empty_rlm_usage()
        run.tombstone = _cancelled_tombstone(usage)
        run.user_turn_id = uuid4()
        run.record_sequence = session.turn_sequence + 1
        session.turn_sequence += 2
        session.history.extend(
            (
                HistoryMessage("user", run.input.text),
                HistoryMessage("assistant", run.tombstone.text, run.tombstone),
            )
        )

    async def turn_records(
        self,
        session_id: UUID,
        access: TurnAccess,
    ) -> tuple[UserTurnRecord | AssistantTurnRecord, ...]:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.access != access:
                raise RunNotFoundError("Turn not found")
            listed = sorted(
                (
                    run
                    for run in self._runs.values()
                    if run.session_id == session_id and run.record_sequence is not None
                ),
                key=lambda run: run.record_sequence or 0,
            )
            records: list[UserTurnRecord | AssistantTurnRecord] = []
            for run in listed:
                committed = run.committed if run.status == "completed" else run.tombstone
                if run.user_turn_id is None or run.record_sequence is None or committed is None:
                    raise RunStateError("listed Run has no durable record")
                records.extend(
                    (
                        UserTurnRecord(
                            run.user_turn_id,
                            session_id,
                            run.record_sequence,
                            run.input,
                            run.run_id,
                        ),
                        AssistantTurnRecord(
                            run.run_id,
                            session_id,
                            run.record_sequence + 1,
                            committed,
                            run.run_id,
                        ),
                    )
                )
            return tuple(records)

    @observe_database_operation("claim")
    async def transition_claim(self, run: ClaimedRun, command: ClaimCommand) -> FailedRunReceipt | None:
        async with self._lock:
            state = self._runs.get(run.run_id)
            if state is None or state.access != run.access or state.session_id != run.session_id:
                raise RunNotFoundError("Turn not found")
            if state.status == "completed":
                raise RunAlreadyCompletedError("Turn already committed")
            return _transition_memory_claim(
                state, run, command, persist_cancel_tombstone=self._persist_cancel_tombstone
            )

    @observe_database_operation("claim")
    async def request_cancel(self, access: TurnAccess, run_id: UUID) -> CancelResult:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.access != access:
                raise RunNotFoundError("Turn not found")
            if run.status != "running":
                return "already_terminal"
            if run.cancel_requested:
                return "already_requested"
            run.cancel_requested = True
            return "requested"

    def _claimed(self, run: ClaimedRun) -> tuple[_RunState, _SessionState]:
        state = self._runs.get(run.run_id)
        session = self._sessions.get(run.session_id)
        if state is None or session is None or state.access != run.access or session.access != run.access:
            raise RunNotFoundError("Turn not found")
        if state.status != "running" or state.claim != run._claim:
            raise RunStateError("Turn is not held by this claim")
        return state, session

    async def reconcile_settling(
        self,
        fence: Callable[[UUID], Awaitable[None]] | None = None,
        *,
        deadline: float | None = None,
    ) -> ReconciliationSummary:
        async with self._lock:
            pending = [
                (run.run_id, run.session_id, run.status, run.claim)
                for run in self._runs.values()
                if run.status in {"running", "settling"}
            ]
        recovered = 0
        fence_failures = 0
        skipped = 0
        budget_exhausted = False
        for index, (run_id, session_id, status, original_claim) in enumerate(pending):
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                skipped += len(pending) - index
                budget_exhausted = True
                break
            async with self._lock:
                current = self._runs.get(run_id)
                if current is None or current.status != status or run_id in self._recovery_runs:
                    skipped += 1
                    continue
                self._recovery_runs.add(run_id)
                current.authority.revoke()
                current.claim = _RunClaimToken(uuid4())
            try:
                try:
                    if fence is not None:
                        await _await_recovery_step(fence(session_id), deadline=deadline)
                except Exception:
                    fence_failures += 1
                    if _recovery_deadline_exhausted(deadline):
                        budget_exhausted = True
                    else:
                        async with self._lock:
                            current = self._runs.get(run_id)
                            if current is not None and current.status == status:
                                current.recovery_attempts += 1
                                current.recovery_last_error = "provider_fence_failed"
                                current.claim = original_claim
                    if budget_exhausted:
                        skipped += len(pending) - index - 1
                        break
                    continue
                async with self._lock:
                    run = self._runs.get(run_id)
                    if run is None or run.status != status:
                        skipped += 1
                    else:
                        if run.status == "running":
                            stale = ClaimFailure("failed", "stale_claim", "Turn failed")
                            revocation = decide_claim_transition(
                                _memory_claim_state(run),
                                RevokeClaim(stale),
                            ).transition
                            if revocation is None or revocation.next_state is None:
                                skipped += 1
                                continue
                            _apply_memory_next_state(
                                run,
                                revocation.next_state,
                                usage=empty_rlm_usage(),
                            )
                        decision = decide_claim_transition(_memory_claim_state(run), CompleteSettlement()).transition
                        if decision is None or decision.next_state is None:
                            skipped += 1
                        else:
                            _apply_memory_next_state(run, decision.next_state)
                            if decision.next_state.status == "cancelled":
                                self._persist_cancel_tombstone(run)
                            run.recovery_attempts = 0
                            run.recovery_last_error = None
                            recovered += 1
            finally:
                async with self._lock:
                    self._recovery_runs.discard(run_id)
        return ReconciliationSummary(
            candidates=len(pending),
            recovered=recovered,
            fence_failures=fence_failures,
            skipped=skipped,
            budget_exhausted=budget_exhausted,
        )


def _expected_claim_conflict(error: IntegrityError) -> bool:
    """Determine whether an integrity error represents a supported claim uniqueness conflict."""
    original = error.orig
    if getattr(original, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE":
        return str(original) in {
            "UNIQUE constraint failed: fleet_runs.session_id",
            "UNIQUE constraint failed: fleet_runs.session_id, fleet_runs.idempotency_key",
        }
    if getattr(original, "sqlstate", getattr(original, "pgcode", None)) != "23505":
        return False
    constraint = getattr(original, "constraint_name", None)
    if constraint is None:
        diagnostic = getattr(original, "diag", None)
        constraint = getattr(diagnostic, "constraint_name", None)
    if constraint is None:
        constraint = getattr(getattr(original, "__cause__", None), "constraint_name", None)
    return constraint in {"uq_fleet_runs_one_running", "uq_fleet_runs_live_idempotency"}


class SqlAlchemyRunStateStore:
    """Transaction-backed authoritative Turn lifecycle state."""

    _RECOVERY_BATCH_SIZE = 100
    _CANCELLATION_PROBE_ATTEMPTS = 2
    _CANCELLATION_PROBE_RETRY_DELAY_SECONDS = 0.05

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        stale_after_seconds: int = 60,
    ) -> None:
        self._sessions = session_factory
        self._stale_after = stale_after_seconds

    @observe_database_operation("claim")
    async def begin(self, request: RunClaim) -> RunStart:
        """Start a run for an active session, reusing an eligible prior run when applicable."""
        try:
            async with self._sessions() as db, db.begin():
                session = await db.scalar(
                    select(SessionRow)
                    .where(
                        SessionRow.id == request.session_id,
                        SessionRow.user_id == request.access.user_id,
                        SessionRow.workspace_id == request.access.workspace_id,
                    )
                    .with_for_update()
                )
                if session is None or session.status != "active":
                    raise RunNotFoundError("Turn not found")
                prior = await db.scalar(
                    select(RunRow)
                    .where(
                        RunRow.session_id == request.session_id,
                        RunRow.idempotency_key == request.idempotency_key,
                        RunRow.status.in_(("running", "settling", "completed")),
                    )
                    .order_by(RunRow.created_at.desc())
                    .limit(1)
                )
                if _prior_run_needs_replay(prior, request):
                    assert prior is not None
                    return await self._replay(db, prior)
                active = await db.scalar(
                    select(RunRow.id).where(
                        RunRow.session_id == request.session_id,
                        RunRow.status.in_(("running", "settling")),
                    )
                )
                _reject_active_run(active is not None)

                claim = _new_run_claim(session.checkpoint_version)
                db.add(
                    RunRow(
                        id=request.proposed_run_id,
                        session_id=request.session_id,
                        status="running",
                        idempotency_key=request.idempotency_key,
                        input_fingerprint=request.input.fingerprint,
                        base_checkpoint_version=session.checkpoint_version,
                        claim_owner=str(claim.value),
                        claim_heartbeat_at=datetime.now(UTC),
                    )
                )
                history = await self._history(db, request.session_id)
        except IntegrityError as exc:
            if not _expected_claim_conflict(exc):
                raise RunLifecycleUnavailableError("Turn lifecycle is unavailable") from exc
            return await self._reconcile_claim_conflict(request)
        except (OSError, SQLAlchemyError) as exc:
            raise RunLifecycleUnavailableError("Turn lifecycle is unavailable") from exc

        async def cancelled() -> bool:
            for attempt in range(self._CANCELLATION_PROBE_ATTEMPTS):
                try:
                    async with self._sessions() as probe_db:
                        value = await probe_db.scalar(
                            select(RunRow.cancel_requested_at).where(RunRow.id == request.proposed_run_id)
                        )
                        return value is not None
                except (OSError, SQLAlchemyError) as exc:
                    if attempt + 1 >= self._CANCELLATION_PROBE_ATTEMPTS:
                        raise DatabaseConnectionError("turn cancellation probe failed") from exc
                    await asyncio.sleep(self._CANCELLATION_PROBE_RETRY_DELAY_SECONDS)
            raise AssertionError("cancellation probe attempts exhausted")

        return ClaimedRun(
            request.proposed_run_id,
            request.session_id,
            request.access,
            request.input,
            history,
            cancelled,
            claim,
        )

    async def _reconcile_claim_conflict(self, request: RunClaim) -> RunStart:
        """Reconcile a claim conflict by reloading the authoritative session and winning run."""
        try:
            async with self._sessions() as db, db.begin():
                session = await db.scalar(
                    select(SessionRow).where(
                        SessionRow.id == request.session_id,
                        SessionRow.user_id == request.access.user_id,
                        SessionRow.workspace_id == request.access.workspace_id,
                        SessionRow.status == "active",
                    )
                )
                if session is None:
                    raise RunNotFoundError("Turn not found")
                prior = await db.scalar(
                    select(RunRow)
                    .where(
                        RunRow.session_id == request.session_id,
                        RunRow.idempotency_key == request.idempotency_key,
                        RunRow.status.in_(("running", "settling", "completed")),
                    )
                    .order_by(RunRow.created_at.desc())
                    .limit(1)
                )
                if _prior_run_needs_replay(prior, request):
                    assert prior is not None
                    return await self._replay(db, prior)
                active = await db.scalar(
                    select(RunRow.id).where(
                        RunRow.session_id == request.session_id,
                        RunRow.status.in_(("running", "settling")),
                    )
                )
                _reject_active_run(active is not None)
        except (OSError, SQLAlchemyError) as exc:
            raise RunLifecycleUnavailableError("Turn lifecycle is unavailable") from exc
        raise RunLifecycleUnavailableError("Turn lifecycle is unavailable")

    @observe_database_operation("commit")
    async def commit(
        self,
        run: ClaimedRun,
        committed: CommittedTurn,
        artifacts: tuple[PromotedArtifact, ...],
        memory_intents: tuple[MemoryPromotionIntent, ...] = (),
    ) -> CommittedTurnReceipt:
        """Persist the committed result and artifacts for a claimed turn."""
        if run.authority.revoked:
            raise RunStateError("Turn claim is invalid")
        async with self._sessions() as db, db.begin():
            return await _commit_sql_run(db, run, committed, artifacts, memory_intents)

    async def transition_claim(self, run: ClaimedRun, command: ClaimCommand) -> FailedRunReceipt | None:
        async with self._sessions() as db, db.begin():
            return await _transition_sql_claim(db, run, command)

    async def request_cancel(self, access: TurnAccess, run_id: UUID) -> CancelResult:
        async with self._sessions() as db, db.begin():
            return await _mark_cancel_requested(db, access, run_id)

    async def _replay(self, db: AsyncSession, run: RunRow) -> CommittedRunReplay:
        return await _committed_replay(db, run)

    @observe_database_operation("recovery")
    async def reconcile_settling(
        self,
        fence: Callable[[UUID], Awaitable[None]] | None = None,
        *,
        deadline: float | None = None,
    ) -> ReconciliationSummary:
        """Recover stale provider claims and report reconciliation outcomes."""
        pending = await self._load_recovery_candidates()
        recovered = 0
        fence_failures = 0
        skipped = 0
        budget_exhausted = False
        for index, pending_run in enumerate(pending):
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                skipped += len(pending) - index
                budget_exhausted = True
                break
            recovery_owner = await self._claim_recovery_owner(pending_run)
            if recovery_owner is None:
                skipped += 1
                continue
            try:
                if fence is not None:
                    await _await_recovery_step(fence(pending_run.session_id), deadline=deadline)
            except Exception:
                fence_failures += 1
                if _recovery_deadline_exhausted(deadline):
                    skipped += len(pending) - index - 1
                    budget_exhausted = True
                    break
                try:
                    await _await_recovery_step(
                        self._restore_after_fence_failure(
                            pending_run,
                            recovery_owner,
                            original_owner=pending_run.claim_owner,
                        ),
                        deadline=deadline,
                    )
                except TimeoutError:
                    skipped += len(pending) - index - 1
                    budget_exhausted = True
                    break
                continue
            completed = await self._complete_recovery(pending_run, recovery_owner)
            if completed:
                recovered += 1
            else:
                skipped += 1
        return ReconciliationSummary(
            candidates=len(pending),
            recovered=recovered,
            fence_failures=fence_failures,
            skipped=skipped,
            budget_exhausted=budget_exhausted,
        )

    async def _load_recovery_candidates(self) -> list[RunRow]:
        async with self._sessions() as db:
            return await _load_recovery_candidates(db, batch_size=self._RECOVERY_BATCH_SIZE)

    async def _claim_recovery_owner(self, pending_run: RunRow) -> str | None:
        async with self._sessions() as db, db.begin():
            return await _claim_recovery_owner(db, pending_run)

    async def _restore_after_fence_failure(
        self,
        pending_run: RunRow,
        recovery_owner: str,
        *,
        original_owner: str | None,
    ) -> None:
        async with self._sessions() as db, db.begin():
            await _restore_after_fence_failure(
                db,
                pending_run,
                recovery_owner,
                original_owner=original_owner,
            )

    async def _complete_recovery(self, pending_run: RunRow, recovery_owner: str) -> bool:
        async with self._sessions() as db, db.begin():
            return await _complete_recovery(db, pending_run, recovery_owner)

    async def _receipt(self, db: AsyncSession, run: RunRow) -> CommittedTurnReceipt:
        return await _committed_receipt(db, run)

    @staticmethod
    async def _history(db: AsyncSession, session_id: UUID) -> SessionHistory:
        return await _session_history(db, session_id)


__all__ = [
    "InMemoryRunStateStore",
    "ReconciliationSummary",
    "SqlAlchemyRunStateStore",
    "_apply_memory_next_state",
    "_apply_row_next_state",
    "_artifact_ref_from_row",
    "_artifact_refs_from_rows",
    "_artifact_row_for_commit",
    "_authorized_sql_session",
    "_await_recovery_step",
    "_cancel_tombstone_rows",
    "_cancelled_tombstone",
    "_claim_client_id",
    "_claim_failure",
    "_claim_owner_matches",
    "_claim_recovery_owner",
    "_claim_snapshot_matches",
    "_command_usage",
    "_commit_memory_run",
    "_commit_sql_run",
    "_committed_output",
    "_committed_receipt",
    "_committed_replay",
    "_complete_recovery",
    "_decode_claim_code",
    "_decode_claim_status",
    "_decode_committed_turn",
    "_decode_failure_code",
    "_decode_failure_status",
    "_decode_turn_input",
    "_encode_committed_turn",
    "_encode_turn_input",
    "_expected_claim_conflict",
    "_heartbeat_unavailable",
    "_history_from_turn_rows",
    "_load_recovery_candidates",
    "_mark_cancel_requested",
    "_memory_claim_state",
    "_memory_intent_row_for_commit",
    "_new_run_claim",
    "_persist_cancel_tombstone",
    "_prior_run_needs_replay",
    "_recovery_deadline_exhausted",
    "_reject_active_run",
    "_restore_after_fence_failure",
    "_row_claim_state",
    "_serialize_sqlite_final_state",
    "_session_history",
    "_touch_claim_heartbeat",
    "_transition_memory_claim",
    "_transition_receipt",
    "_transition_sql_claim",
    "_turn_failure",
    "_validate_completed_replay_state",
    "_validate_sql_claim",
]
