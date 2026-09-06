"""In-memory Turn state adapter; SQL follows the same atomic contract."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.artifacts.models import ArtifactRef
from fleet_rlm.artifacts.promotion import PromotedArtifact
from fleet_rlm.chat.run_authority import RunAuthority
from fleet_rlm.chat.run_claim import (
    ClaimCommand,
    ClaimFailure,
    CompleteSettlement,
    RevokeClaim,
    decide_claim_transition,
)
from fleet_rlm.chat.run_lifecycle import (
    CancelResult,
    ClaimedRun,
    CommittedRunReplay,
    CommittedTurnReceipt,
    FailedRunReceipt,
    RunAlreadyCompletedError,
    RunClaim,
    RunFailure,
    RunFailureCode,
    RunLifecycleUnavailableError,
    RunNotFoundError,
    RunStart,
    RunStateError,
    _RunClaimToken,
)
from fleet_rlm.persistence.database import DatabaseConnectionError
from fleet_rlm.persistence.models import RunRow, SessionRow
from fleet_rlm.persistence.repositories.run_claim_decisions import (
    _new_run_claim,
    _prior_run_needs_replay,
    _reject_active_run,
    _validate_completed_replay_state,
)
from fleet_rlm.persistence.repositories.run_codec import (
    _apply_memory_next_state,
    _cancelled_tombstone,
    _memory_claim_state,
)
from fleet_rlm.persistence.repositories.run_final_state import (
    _commit_memory_run,
    _commit_sql_run,
    _transition_memory_claim,
    _transition_sql_claim,
)
from fleet_rlm.persistence.repositories.run_liveness import (
    _await_recovery_step,
    _claim_recovery_owner,
    _complete_recovery,
    _load_recovery_candidates,
    _mark_cancel_requested,
    _recovery_deadline_exhausted,
    _restore_after_fence_failure,
)
from fleet_rlm.persistence.repositories.run_queries import _committed_receipt, _committed_replay, _session_history
from fleet_rlm.runtime.usage import RLMUsage, empty_rlm_usage
from fleet_rlm.sessions.committed_turn import CommittedTurn
from fleet_rlm.sessions.models import (
    AssistantTurnRecord,
    HistoryMessage,
    SessionHistory,
    TurnAccess,
    TurnInput,
    UserTurnRecord,
)
from fleet_rlm.workspace.memory import MemoryPromotionIntent


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
        # The credential-free private composition keeps promotion in-process;
        # the SQL store owns the durable outbox, so intents are ignored here.
        del memory_intents
        async with self._lock:
            if run.authority.revoked:
                raise RunStateError("Turn claim is invalid")
            state, session = self._claimed(run)
            return _commit_memory_run(state, session, run, committed, artifacts)

    def _persist_cancel_tombstone(self, run: _RunState, *, usage: RLMUsage | None = None) -> None:
        """Persist the bounded D2 tombstone for a claim transitioning to terminal cancelled."""
        session = self._sessions.get(run.session_id)
        if session is None or run.tombstone is not None:
            return
        if usage is None:
            usage = run.terminal_intent.usage if run.terminal_intent is not None else empty_rlm_usage()
        run.tombstone = _cancelled_tombstone(usage)
        run.user_turn_id = uuid4()
        run.record_sequence = session.turn_sequence + 1
        session.turn_sequence += 2
        # Keep the bounded tombstone pair in the Session History/audit
        # projection.  Its originating CommittedTurn metadata lets the
        # model-facing canonical projection exclude this non-conversation
        # result without losing durable listing/retry semantics.
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
        """Recover prior-process claims with the same bounded policy as SQL."""
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
    """Recognize only claim uniqueness conflicts, never FK/check failures."""
    original = error.orig
    if getattr(original, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE":
        return str(original) in {
            "UNIQUE constraint failed: fleet_runs.session_id",
            "UNIQUE constraint failed: fleet_runs.session_id, fleet_runs.idempotency_key",
        }
    if getattr(original, "sqlstate", getattr(original, "pgcode", None)) != "23505":
        return False
    # asyncpg exposes the constraint name directly on the driver exception;
    # SQLAlchemy/other adapters may retain it under ``diag`` or ``__cause__``.
    # Check the real asyncpg shape first, then keep the compatibility fallbacks.
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

    async def begin(self, request: RunClaim) -> RunStart:
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
        """Read the winning claim once after rollback, retaining Session authority."""
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

    async def commit(
        self,
        run: ClaimedRun,
        committed: CommittedTurn,
        artifacts: tuple[PromotedArtifact, ...],
        memory_intents: tuple[MemoryPromotionIntent, ...] = (),
    ) -> CommittedTurnReceipt:
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

    async def reconcile_settling(
        self,
        fence: Callable[[UUID], Awaitable[None]] | None = None,
        *,
        deadline: float | None = None,
    ) -> ReconciliationSummary:
        """Recover stale provider claims and report reconciliation outcomes.

        Parameters:
            fence (Callable[[UUID], Awaitable[None]] | None): Optional callback used
                to verify provider state before completing recovery.
            deadline (float | None): Optional monotonic deadline for the recovery operation.

        Returns:
            ReconciliationSummary: Counts of candidates, recovered runs, provider
                fence failures, skipped runs, and whether the deadline was exhausted.
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
        """Load bounded recovery candidates through the facade-owned session."""
        async with self._sessions() as db:
            return await _load_recovery_candidates(db, batch_size=self._RECOVERY_BATCH_SIZE)

    async def _claim_recovery_owner(self, pending_run: RunRow) -> str | None:
        """Take recovery ownership in one facade-owned transaction."""
        async with self._sessions() as db, db.begin():
            return await _claim_recovery_owner(db, pending_run)

    async def _restore_after_fence_failure(
        self,
        pending_run: RunRow,
        recovery_owner: str,
        *,
        original_owner: str | None,
    ) -> None:
        """Restore ownership in one facade-owned transaction."""
        async with self._sessions() as db, db.begin():
            await _restore_after_fence_failure(
                db,
                pending_run,
                recovery_owner,
                original_owner=original_owner,
            )

    async def _complete_recovery(self, pending_run: RunRow, recovery_owner: str) -> bool:
        """Complete recovery in one facade-owned transaction."""
        async with self._sessions() as db, db.begin():
            return await _complete_recovery(db, pending_run, recovery_owner)

    async def _receipt(self, db: AsyncSession, run: RunRow) -> CommittedTurnReceipt:
        return await _committed_receipt(db, run)

    @staticmethod
    async def _history(db: AsyncSession, session_id: UUID) -> SessionHistory:
        return await _session_history(db, session_id)
