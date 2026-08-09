"""In-memory Turn state adapter; SQL follows the same atomic contract."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.artifacts.models import ArtifactRef
from fleet_rlm.artifacts.promotion import PromotedArtifact
from fleet_rlm.artifacts.safety import parse_kind
from fleet_rlm.chat.turn_claim import (
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
)
from fleet_rlm.chat.turn_detail_policy import commit_cancelled_tombstone
from fleet_rlm.chat.turn_lifecycle import (
    BeginTurn,
    CancelResult,
    CommittedTurnReceipt,
    ExecuteTurn,
    FailedRunReceipt,
    FailureCode,
    ReplayTurn,
    TurnAlreadyCompletedError,
    TurnFailure,
    TurnIdempotencyMismatchError,
    TurnInProgressError,
    TurnLifecycleUnavailableError,
    TurnNotFoundError,
    TurnStart,
    TurnStateError,
    _TurnClaimToken,
    failure_code_for_terminal_status,
)
from fleet_rlm.persistence.database import DatabaseConnectionError
from fleet_rlm.persistence.models import ArtifactRow, RunRow, SessionRow, TurnRow
from fleet_rlm.rlm.dspy_contract import RLMUsage, empty_rlm_usage
from fleet_rlm.sessions.committed_turn import CommittedTurn, CommittedTurnCodec
from fleet_rlm.sessions.models import (
    AssistantTurnRecord,
    HistoryMessage,
    SessionHistory,
    TurnAccess,
    TurnInput,
    TurnInputCodec,
    UserTurnRecord,
)


@dataclass(slots=True)
class _RunState:
    run_id: UUID
    session_id: UUID
    access: TurnAccess
    idempotency_key: str
    input_fingerprint: str
    input: TurnInput
    claim: _TurnClaimToken
    status: Literal["running", "settling", "completed", "failed", "cancelled", "timeout"]
    failure_code: FailureCode | None = None
    terminal_intent: TurnFailure | None = None
    cancel_requested: bool = False
    committed: CommittedTurn | None = None
    checkpoint_version: int | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    user_turn_id: UUID | None = None
    tombstone: CommittedTurn | None = None
    record_sequence: int | None = None


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


async def _await_recovery_step(awaitable: Awaitable[Any], *, deadline: float | None) -> Any:
    """Await one recovery operation without exceeding the shared startup deadline."""
    if deadline is None:
        return await awaitable
    async with asyncio.timeout_at(deadline):
        return await awaitable


def _recovery_deadline_exhausted(deadline: float | None) -> bool:
    """Determine whether the recovery deadline has been reached.
    
    Parameters:
    	deadline (float | None): Monotonic time at which recovery must stop, or `None` for no deadline.
    
    Returns:
    	`true` if the deadline has been reached, `false` otherwise.
    """
    return deadline is not None and asyncio.get_running_loop().time() >= deadline


def _decode_failure_status(value: str) -> Literal["failed", "cancelled", "timeout"]:
    """Validate and return a persisted run failure status.
    
    Parameters:
    	value (str): The persisted failure status.
    
    Returns:
    	The validated failure status.
    
    Raises:
    	TurnStateError: If the value is not a supported failure status.
    """
    if value in {"failed", "cancelled", "timeout"}:
        return value
    raise TurnStateError("persisted Run has an invalid failure status")


def _decode_failure_code(
    value: str | None,
    *,
    status: Literal["failed", "cancelled", "timeout"],
) -> FailureCode:
    if value in {"preparation_failed", "execution_failed", "commit_failed", "cancelled", "timeout", "stale_claim"}:
        return value
    if value in {None, "failed"}:
        return failure_code_for_terminal_status(status)
    raise TurnStateError("persisted Run has an invalid failure code")


def _decode_claim_status(value: str) -> ClaimStatus:
    if value in {"running", "settling", "completed", "failed", "cancelled", "timeout"}:
        return value
    raise TurnStateError("persisted Run has an invalid claim status")


def _decode_claim_code(value: str | None) -> ClaimFailureCode | None:
    if value is None:
        return None
    if value in {"preparation_failed", "execution_failed", "commit_failed", "cancelled", "timeout", "stale_claim"}:
        return value
    raise TurnStateError("persisted Run has an invalid failure code")


def _claim_failure(failure: TurnFailure) -> ClaimFailure:
    return ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message)


def _command_usage(command: ClaimCommand) -> RLMUsage | None:
    if isinstance(command, (FailClaim, BeginSettlement, RevokeClaim)):
        return cast(RLMUsage, command.usage)
    return None


def _turn_failure(intent: ClaimFailure, usage: RLMUsage) -> TurnFailure:
    return TurnFailure(intent.status, intent.code, intent.public_message, usage)


def _memory_claim_state(run: _RunState) -> ClaimState:
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
    run: _RunState,
    next_state: ClaimState,
    *,
    usage: RLMUsage | None = None,
) -> None:
    run.status = cast(Any, next_state.status)
    run.failure_code = cast(FailureCode, next_state.failure_code)
    if next_state.intent is not None:
        if usage is None:
            raise TurnStateError("claim intent application requires usage")
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


class InMemoryTurnStateStore:
    """Lock-backed parity adapter for private composition and lifecycle tests."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: dict[UUID, _SessionState] = {}
        self._runs: dict[UUID, _RunState] = {}
        self._keys: dict[tuple[UUID, str], UUID] = {}

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
                raise TurnNotFoundError("Turn not found")
            session.status = status

    async def begin(self, request: BeginTurn) -> TurnStart:
        async with self._lock:
            session = self._sessions.get(request.session_id)
            if session is None or session.access != request.access or session.status != "active":
                raise TurnNotFoundError("Turn not found")
            key = (request.session_id, request.idempotency_key)
            prior_id = self._keys.get(key)
            if prior_id is not None:
                prior = self._runs[prior_id]
                if prior.input_fingerprint not in request.input.acceptable_fingerprints:
                    raise TurnIdempotencyMismatchError("idempotency key is bound to different input")
                if prior.status in {"running", "settling"}:
                    raise TurnInProgressError("Turn is already running")
                if prior.status == "completed":
                    if prior.committed is None or prior.checkpoint_version is None:
                        raise TurnStateError("completed Run has no committed Turn")
                    return ReplayTurn(
                        prior.run_id,
                        prior.session_id,
                        prior.committed,
                        prior.checkpoint_version,
                    )
            if any(
                run.session_id == request.session_id and run.status in {"running", "settling"}
                for run in self._runs.values()
            ):
                raise TurnInProgressError("Session already has a running Turn")

            claim = _TurnClaimToken(uuid4(), session.checkpoint_version)
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

            return ExecuteTurn(
                run.run_id,
                run.session_id,
                run.access,
                request.input,
                SessionHistory(tuple(session.history)),
                cancelled,
                claim,
            )

    async def commit(
        self,
        turn: ExecuteTurn,
        committed: CommittedTurn,
        artifacts: tuple[PromotedArtifact, ...],
    ) -> CommittedTurnReceipt:
        async with self._lock:
            if turn.authority.revoked:
                raise TurnStateError("Turn claim is invalid")
            run, session = self._claimed(turn)
            session.history.extend(
                (
                    HistoryMessage("user", turn.input.text),
                    HistoryMessage("assistant", committed.text),
                )
            )
            session.checkpoint_version += 1
            run.status = "completed"
            run.user_turn_id = uuid4()
            run.record_sequence = session.turn_sequence + 1
            session.turn_sequence += 2
            run.committed = committed
            run.checkpoint_version = session.checkpoint_version
            refs = tuple(item.ref for item in artifacts)
            run.artifacts = refs
            return CommittedTurnReceipt(run.run_id, session.checkpoint_version, committed, refs)

    def _persist_cancel_tombstone(self, run: _RunState, *, usage: RLMUsage | None = None) -> None:
        """Persist the bounded D2 tombstone for a claim transitioning to terminal cancelled."""
        session = self._sessions.get(run.session_id)
        if session is None or run.tombstone is not None:
            return
        if usage is None:
            usage = run.terminal_intent.usage if run.terminal_intent is not None else empty_rlm_usage()
        run.tombstone = commit_cancelled_tombstone(usage)
        run.user_turn_id = uuid4()
        run.record_sequence = session.turn_sequence + 1
        session.turn_sequence += 2
        session.history.extend(
            (
                HistoryMessage("user", run.input.text),
                HistoryMessage("assistant", run.tombstone.text),
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
                raise TurnNotFoundError("Turn not found")
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
                    raise TurnStateError("listed Run has no durable record")
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

    async def transition_claim(self, turn: ExecuteTurn, command: ClaimCommand) -> FailedRunReceipt | None:
        async with self._lock:
            run = self._runs.get(turn.run_id)
            if run is None or run.access != turn.access or run.session_id != turn.session_id:
                raise TurnNotFoundError("Turn not found")
            if run.status == "completed":
                raise TurnAlreadyCompletedError("Turn already committed")
            stale_terminal = (
                isinstance(command, CompleteSettlement) and run.status == "failed" and run.failure_code == "stale_claim"
            )
            if not isinstance(command, RevokeClaim) and not stale_terminal and run.claim != turn._claim:
                raise TurnStateError("Turn claim is invalid")
            try:
                decision = decide_claim_transition(_memory_claim_state(run), command)
            except InvalidClaimTransitionError as exc:
                raise TurnStateError(str(exc)) from exc
            if isinstance(command, HeartbeatClaim):
                if not decision.heartbeat_allowed:
                    raise TurnStateError("Turn claim is invalid")
                return None
            transition = decision.transition
            if transition is None:
                raise TurnStateError("claim decision did not include a transition")
            if transition.next_state is not None:
                _apply_memory_next_state(run, transition.next_state, usage=_command_usage(command))
                if transition.next_state.status == "cancelled":
                    self._persist_cancel_tombstone(run, usage=_command_usage(command))
            return _transition_receipt(run.run_id, transition)

    async def request_cancel(self, access: TurnAccess, run_id: UUID) -> CancelResult:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.access != access:
                raise TurnNotFoundError("Turn not found")
            if run.status != "running":
                return "already_terminal"
            if run.cancel_requested:
                return "already_requested"
            run.cancel_requested = True
            return "requested"

    def _claimed(self, turn: ExecuteTurn) -> tuple[_RunState, _SessionState]:
        run = self._runs.get(turn.run_id)
        session = self._sessions.get(turn.session_id)
        if run is None or session is None or run.access != turn.access or session.access != turn.access:
            raise TurnNotFoundError("Turn not found")
        if run.status != "running" or run.claim != turn._claim:
            raise TurnStateError("Turn is not held by this claim")
        return run, session

    async def reconcile_settling(
        self,
        fence: Callable[[UUID], Awaitable[None]] | None = None,
        *,
        deadline: float | None = None,
    ) -> ReconciliationSummary:
        """In-memory workers cannot survive the process that owned them."""
        async with self._lock:
            pending = [run for run in self._runs.values() if run.status == "settling"]
        recovered = 0
        fence_failures = 0
        skipped = 0
        budget_exhausted = False
        for index, pending_run in enumerate(pending):
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                skipped += len(pending) - index
                budget_exhausted = True
                break
            try:
                if fence is not None:
                    await fence(pending_run.session_id)
            except Exception:
                fence_failures += 1
                continue
            async with self._lock:
                run = self._runs.get(pending_run.run_id)
                if run is not None and run.status == "settling" and run.terminal_intent is not None:
                    decision = decide_claim_transition(_memory_claim_state(run), CompleteSettlement()).transition
                    if decision is not None and decision.next_state is not None:
                        _apply_memory_next_state(run, decision.next_state)
                        if decision.next_state.status == "cancelled":
                            self._persist_cancel_tombstone(run)
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


class SqlAlchemyTurnStateStore:
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

    async def begin(self, request: BeginTurn) -> TurnStart:
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
                    raise TurnNotFoundError("Turn not found")
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
                if prior is not None:
                    if prior.input_fingerprint not in request.input.acceptable_fingerprints:
                        raise TurnIdempotencyMismatchError("idempotency key is bound to different input")
                    if prior.status in {"running", "settling"}:
                        raise TurnInProgressError("Turn is already running")
                    return await self._replay(db, prior)
                active = await db.scalar(
                    select(RunRow.id).where(
                        RunRow.session_id == request.session_id,
                        RunRow.status.in_(("running", "settling")),
                    )
                )
                if active is not None:
                    raise TurnInProgressError("Session already has a running Turn")

                claim = _TurnClaimToken(uuid4(), session.checkpoint_version)
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
        except (OSError, SQLAlchemyError) as exc:
            raise TurnLifecycleUnavailableError("Turn lifecycle is unavailable") from exc

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

        return ExecuteTurn(
            request.proposed_run_id,
            request.session_id,
            request.access,
            request.input,
            history,
            cancelled,
            claim,
        )

    async def commit(
        self,
        turn: ExecuteTurn,
        committed: CommittedTurn,
        artifacts: tuple[PromotedArtifact, ...],
    ) -> CommittedTurnReceipt:
        async with self._sessions() as db, db.begin():
            if turn.authority.revoked:
                raise TurnStateError("Turn claim is invalid")
            run = await db.get(RunRow, turn.run_id, with_for_update=True)
            if run is None or run.session_id != turn.session_id:
                raise TurnNotFoundError("Turn not found")
            if run.status == "completed":
                return await self._receipt(db, run)
            session = await db.scalar(
                select(SessionRow)
                .where(
                    SessionRow.id == turn.session_id,
                    SessionRow.user_id == turn.access.user_id,
                    SessionRow.workspace_id == turn.access.workspace_id,
                )
                .with_for_update()
            )
            if session is None:
                raise TurnNotFoundError("Turn not found")
            if (
                run.status != "running"
                or run.claim_owner != str(turn._claim.value)
                or run.base_checkpoint_version != turn._claim.base_checkpoint_version
                or session.checkpoint_version != turn._claim.base_checkpoint_version
            ):
                raise TurnStateError("Turn claim or Checkpoint is stale")
            last_sequence = int(
                await db.scalar(
                    select(func.coalesce(func.max(TurnRow.sequence), 0)).where(TurnRow.session_id == turn.session_id)
                )
                or 0
            )
            db.add_all(
                (
                    TurnRow(
                        id=uuid4(),
                        session_id=turn.session_id,
                        run_id=turn.run_id,
                        sequence=last_sequence + 1,
                        role="user",
                        user_input_json=TurnInputCodec.encode(turn.input),
                        committed_turn_json=None,
                    ),
                    TurnRow(
                        id=turn.run_id,
                        session_id=turn.session_id,
                        run_id=turn.run_id,
                        sequence=last_sequence + 2,
                        role="assistant",
                        user_input_json=None,
                        committed_turn_json=CommittedTurnCodec.encode(committed),
                    ),
                )
            )
            for item in artifacts:
                ref = item.ref
                db.add(
                    ArtifactRow(
                        id=ref.id,
                        user_id=turn.access.user_id,
                        workspace_id=turn.access.workspace_id,
                        session_id=ref.session_id,
                        run_id=ref.run_id,
                        kind=ref.kind,
                        title=ref.title,
                        media_type=ref.media_type,
                        byte_size=ref.byte_size,
                        checksum_sha256=ref.checksum_sha256,
                        storage_ref=item.storage_ref,
                    )
                )
            session.checkpoint_version += 1
            run.status = "completed"
            run.commit_checkpoint_version = session.checkpoint_version
            run.finished_at = datetime.now(UTC)
            run.claim_owner = None
            run.claim_heartbeat_at = None
            if turn.authority.revoked:
                raise TurnStateError("Turn claim is invalid")
            return CommittedTurnReceipt(
                turn.run_id,
                session.checkpoint_version,
                committed,
                tuple(item.ref for item in artifacts),
            )

    async def transition_claim(self, turn: ExecuteTurn, command: ClaimCommand) -> FailedRunReceipt | None:
        async with self._sessions() as db, db.begin():
            run = await db.get(RunRow, turn.run_id, with_for_update=True)
            if run is None or run.session_id != turn.session_id:
                raise TurnNotFoundError("Turn not found")
            if run.status == "completed":
                raise TurnAlreadyCompletedError("Turn already committed")
            stale_terminal = (
                isinstance(command, CompleteSettlement) and run.status == "failed" and run.failure_code == "stale_claim"
            )
            if (
                not isinstance(command, RevokeClaim)
                and not stale_terminal
                and run.claim_owner != str(turn._claim.value)
            ):
                raise TurnStateError("Turn claim is invalid")
            try:
                decision = decide_claim_transition(_row_claim_state(run), command)
            except InvalidClaimTransitionError as exc:
                raise TurnStateError(str(exc)) from exc
            if isinstance(command, HeartbeatClaim):
                if not decision.heartbeat_allowed or run.claim_owner != str(turn._claim.value):
                    raise TurnStateError("Turn claim is invalid")
                run.claim_heartbeat_at = datetime.now(UTC)
                return None
            transition = decision.transition
            if transition is None:
                raise TurnStateError("claim decision did not include a transition")
            if transition.next_state is not None:
                _apply_row_next_state(
                    run,
                    transition.next_state,
                    public_message=transition.public_message,
                    usage=_command_usage(command),
                )
                if isinstance(command, (FailClaim, CompleteSettlement)):
                    # The cancelled terminal shape (claim released) must flush
                    # before the tombstone's sequence read autoflushes the row.
                    run.finished_at = datetime.now(UTC)
                    run.claim_owner = None
                    run.claim_heartbeat_at = None
                elif isinstance(command, (BeginSettlement, RevokeClaim)):
                    run.recovery_metadata_json = {"cleanup": "pending"}
                if transition.next_state.status == "cancelled":
                    await self._persist_cancel_tombstone(db, run, turn.input)
            return _transition_receipt(run.id, transition)

    @staticmethod
    async def _persist_cancel_tombstone(db: AsyncSession, run: RunRow, turn_input: TurnInput | None) -> None:
        """Persist the bounded D2 tombstone atomically with the cancelled terminal transition.

        Settle-time callers pass the claimed Turn input; startup recovery has no
        input snapshot and persists the assistant tombstone alone.
        """
        usage: RLMUsage = cast(RLMUsage, run.failure_usage_json) if run.failure_usage_json else empty_rlm_usage()
        tombstone = commit_cancelled_tombstone(usage)
        last_sequence = int(
            await db.scalar(
                select(func.coalesce(func.max(TurnRow.sequence), 0)).where(TurnRow.session_id == run.session_id)
            )
            or 0
        )
        next_sequence = last_sequence + 1
        if turn_input is not None:
            db.add(
                TurnRow(
                    id=uuid4(),
                    session_id=run.session_id,
                    run_id=run.id,
                    sequence=next_sequence,
                    role="user",
                    user_input_json=TurnInputCodec.encode(turn_input),
                    committed_turn_json=None,
                )
            )
            next_sequence += 1
        db.add(
            TurnRow(
                id=run.id,
                session_id=run.session_id,
                run_id=run.id,
                sequence=next_sequence,
                role="assistant",
                user_input_json=None,
                committed_turn_json=CommittedTurnCodec.encode(tombstone),
            )
        )

    async def request_cancel(self, access: TurnAccess, run_id: UUID) -> CancelResult:
        async with self._sessions() as db, db.begin():
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
                raise TurnNotFoundError("Turn not found")
            if run.status != "running":
                return "already_terminal"
            if run.cancel_requested_at is not None:
                return "already_requested"
            run.cancel_requested_at = datetime.now(UTC)
            return "requested"

    async def _replay(self, db: AsyncSession, run: RunRow) -> ReplayTurn:
        receipt = await self._receipt(db, run)
        return ReplayTurn(run.id, run.session_id, receipt.committed_turn, receipt.checkpoint_version)

    async def reconcile_settling(
        self,
        fence: Callable[[UUID], Awaitable[None]] | None = None,
        *,
        deadline: float | None = None,
    ) -> ReconciliationSummary:
        """Recover stale provider claims and report reconciliation outcomes.
        
        Parameters:
            fence (Callable[[UUID], Awaitable[None]] | None): Optional callback used to verify provider state before completing recovery.
            deadline (float | None): Optional monotonic deadline for the recovery operation.
        
        Returns:
            ReconciliationSummary: Counts of candidates, recovered runs, provider fence failures, skipped runs, and whether the deadline was exhausted.
        """
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
                    await fence(pending_run.session_id)
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
            if await self._complete_recovery(pending_run, recovery_owner):
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
        """Load bounded nonterminal claims that require startup ownership recovery."""
        async with self._sessions() as db:
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
                        .limit(self._RECOVERY_BATCH_SIZE)
                    )
                ).all()
            )

    async def _claim_recovery_owner(self, pending_run: RunRow) -> str | None:
        """Take conditional ownership before any provider call is awaited."""
        owner = pending_run.claim_owner
        heartbeat = pending_run.claim_heartbeat_at
        if owner is None or heartbeat is None:
            return None
        recovery_owner = f"recovery:{uuid4()}"
        async with self._sessions() as db, db.begin():
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
        self,
        pending_run: RunRow,
        recovery_owner: str,
        *,
        original_owner: str | None,
    ) -> None:
        """Restore the prior owner and record retry metadata after a fence failure."""
        async with self._sessions() as db, db.begin():
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

    async def _complete_recovery(self, pending_run: RunRow, recovery_owner: str) -> bool:
        """Apply the stale-claim transition and release recovery ownership atomically."""
        async with self._sessions() as db, db.begin():
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
                # Flush the cancelled terminal shape before the tombstone reads.
                await self._persist_cancel_tombstone(db, run, None)
            return True

    async def _receipt(self, db: AsyncSession, run: RunRow) -> CommittedTurnReceipt:
        row = await db.scalar(select(TurnRow).where(TurnRow.run_id == run.id, TurnRow.role == "assistant"))
        if row is None or row.committed_turn_json is None or run.commit_checkpoint_version is None:
            raise TurnStateError("completed Run has no committed Turn")
        committed = CommittedTurnCodec.decode(row.committed_turn_json)
        artifact_rows = (
            await db.scalars(select(ArtifactRow).where(ArtifactRow.run_id == run.id).order_by(ArtifactRow.created_at))
        ).all()
        artifacts = tuple(
            ArtifactRef(
                item.id,
                item.session_id,
                item.run_id,
                parse_kind(item.kind),
                item.title,
                item.media_type,
                item.byte_size,
                item.checksum_sha256 or "",
            )
            for item in artifact_rows
        )
        return CommittedTurnReceipt(run.id, run.commit_checkpoint_version, committed, artifacts)

    @staticmethod
    async def _history(db: AsyncSession, session_id: UUID) -> SessionHistory:
        rows = (
            await db.scalars(select(TurnRow).where(TurnRow.session_id == session_id).order_by(TurnRow.sequence))
        ).all()
        messages: list[HistoryMessage] = []
        for row in rows:
            if row.role == "user" and row.user_input_json is not None:
                messages.append(HistoryMessage("user", TurnInputCodec.decode(row.user_input_json).text))
            elif row.role == "assistant" and row.committed_turn_json is not None:
                messages.append(HistoryMessage("assistant", CommittedTurnCodec.decode(row.committed_turn_json).text))
            else:
                raise TurnStateError("stored Turn shape is invalid")
        return SessionHistory(tuple(messages))
