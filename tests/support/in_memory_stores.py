"""In-memory test doubles for Session and Turn persistence."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from fleet_rlm.artifacts.models import ArtifactRef
from fleet_rlm.artifacts.promotion import PromotedArtifact
from fleet_rlm.persistence.database import observe_database_operation
from fleet_rlm.persistence.repositories.run_state import (
    ReconciliationSummary,
    _await_recovery_step,
    _cancelled_tombstone,
    _command_usage,
    _decode_claim_code,
    _decode_claim_status,
    _new_run_claim,
    _prior_run_needs_replay,
    _recovery_deadline_exhausted,
    _reject_active_run,
    _transition_receipt,
    _turn_failure,
    _validate_completed_replay_state,
)
from fleet_rlm.persistence.repositories.sessions import (
    SequenceCursor,
    SessionNotFoundError,
    SessionPage,
    SessionRecord,
    SessionTurnPage,
)
from fleet_rlm.sessions.bindings import SandboxBinding, validate_sandbox_binding
from fleet_rlm.sessions.committed_turn import CommittedTurn
from fleet_rlm.sessions.models import (
    AssistantTurnRecord,
    HistoryMessage,
    SessionHistory,
    TurnAccess,
    TurnInput,
    UserTurnRecord,
)
from fleet_rlm.sessions.run_claim import (
    ClaimCommand,
    ClaimFailure,
    ClaimState,
    CompleteSettlement,
    HeartbeatClaim,
    InvalidClaimTransitionError,
    RevokeClaim,
    decide_claim_transition,
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
    RunNotFoundError,
    RunStart,
    RunStateError,
    _RunClaimToken,
)
from fleet_rlm.sessions.usage import RLMUsage, empty_rlm_usage
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


def _claim_failure(failure: RunFailure) -> ClaimFailure:
    return ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message)


def _memory_claim_state(run: Any) -> ClaimState:
    intent = _claim_failure(run.terminal_intent) if run.terminal_intent is not None else None
    return ClaimState(_decode_claim_status(run.status), _decode_claim_code(run.failure_code), intent)


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


class InMemorySessionCatalog:
    """In-memory Session Catalog adapter sharing authoritative Turn state registration."""

    def __init__(self, turns: InMemoryRunStateStore) -> None:
        self._turns = turns
        self._records: dict[UUID, SessionRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, *, user_id: UUID, workspace_id: UUID, title: str) -> SessionRecord:
        now = datetime.now(UTC)
        record = SessionRecord(uuid4(), user_id, workspace_id, "active", title, 0, now, now)
        async with self._lock:
            self._records[record.id] = record
        await self._turns.add_session(record.id, TurnAccess(user_id, workspace_id))
        return record

    async def list(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        status: str | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> SessionPage:
        async with self._lock:
            values = [
                record
                for record in self._records.values()
                if record.user_id == user_id
                and record.workspace_id == workspace_id
                and (status is None or record.status == status)
                and (not search or search.lower() in record.title.lower())
            ]
        values.sort(key=lambda item: (item.updated_at or datetime.min.replace(tzinfo=UTC), item.id), reverse=True)
        return SessionPage(tuple(values[offset : offset + limit]), len(values))

    async def get(self, session_id: UUID, *, user_id: UUID, workspace_id: UUID) -> SessionRecord:
        async with self._lock:
            record = self._records.get(session_id)
        if record is None or record.user_id != user_id or record.workspace_id != workspace_id:
            raise SessionNotFoundError("session not found")
        return record

    async def update(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        title: str | None,
        status: str | None,
    ) -> SessionRecord:
        record = await self.get(session_id, user_id=user_id, workspace_id=workspace_id)
        updated = SessionRecord(
            record.id,
            record.user_id,
            record.workspace_id,
            status or record.status,
            title if title is not None else record.title,
            record.checkpoint_version,
            record.created_at,
            datetime.now(UTC),
        )
        async with self._lock:
            self._records[session_id] = updated
        await self._turns.set_session_status(
            session_id,
            TurnAccess(user_id, workspace_id),
            cast(Literal["active", "archived"], updated.status),
        )
        return updated

    async def archive(self, session_id: UUID, *, user_id: UUID, workspace_id: UUID) -> SessionRecord:
        return await self.update(
            session_id,
            user_id=user_id,
            workspace_id=workspace_id,
            title=None,
            status="archived",
        )

    async def turns(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        cursor: SequenceCursor,
        limit: int,
    ) -> SessionTurnPage:
        await self.get(session_id, user_id=user_id, workspace_id=workspace_id)
        records = await self._turns.turn_records(session_id, TurnAccess(user_id, workspace_id))
        selected = tuple(
            item for item in records if cursor.after_sequence is None or item.sequence > cursor.after_sequence
        )
        page = selected[:limit]
        next_cursor = page[-1].sequence if len(selected) > limit and page else None
        return SessionTurnPage(page, next_cursor)


class InMemorySandboxBindingStore:
    """Test/local binding store that does not require SQL."""

    def __init__(self) -> None:
        self._items: dict[UUID, SandboxBinding] = {}

    async def get(self, session_id: UUID) -> SandboxBinding | None:
        return self._items.get(session_id)

    async def get_scoped(self, session_id: UUID, *, workspace_id: UUID) -> SandboxBinding | None:
        binding = self._items.get(session_id)
        if binding is None or binding.workspace_id != workspace_id:
            return None
        return binding

    async def upsert(self, binding: SandboxBinding) -> SandboxBinding:
        validate_sandbox_binding(binding)
        existing = self._items.get(binding.session_id)
        if existing is not None and existing.workspace_id != binding.workspace_id:
            raise ValueError("sandbox binding workspace scope mismatch")
        if existing is not None and binding.generation < existing.generation:
            raise ValueError("stale sandbox binding generation")
        if (
            existing is not None
            and binding.generation == existing.generation
            and binding.sandbox_id != existing.sandbox_id
        ):
            raise ValueError("conflicting sandbox binding identity for generation")
        if (
            existing is not None
            and binding.generation == existing.generation
            and existing.sandbox_id == binding.sandbox_id
            and existing.provider_state != "running"
            and binding.provider_state == "running"
        ):
            raise ValueError("stale running sandbox binding generation")
        self._items[binding.session_id] = binding
        return binding

    async def replace_with_next_generation(self, binding: SandboxBinding) -> SandboxBinding:
        """Atomically allocate the next identity generation for local state."""
        validate_sandbox_binding(binding)
        existing = self._items.get(binding.session_id)
        if existing is not None and existing.workspace_id != binding.workspace_id:
            raise ValueError("sandbox binding workspace scope mismatch")
        if existing is None:
            generation = binding.generation
        elif existing.sandbox_id == binding.sandbox_id and existing.provider_state == "running":
            generation = existing.generation
        else:
            generation = existing.generation + 1
        replacement = replace(binding, generation=generation)
        self._items[binding.session_id] = replacement
        return replacement
