"""In-memory Turn state adapter; SQL follows the same atomic contract."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.artifacts.models import ArtifactRef
from fleet_rlm.artifacts.promotion import PromotedArtifact
from fleet_rlm.artifacts.safety import parse_kind
from fleet_rlm.chat.turn_lifecycle import (
    BeginTurn,
    CancelResult,
    CommittedTurnReceipt,
    ExecuteTurn,
    FailedRunReceipt,
    ReplayTurn,
    TurnFailure,
    TurnIdempotencyMismatchError,
    TurnInProgressError,
    TurnNotFoundError,
    TurnStart,
    TurnStateError,
    _TurnClaimToken,
)
from fleet_rlm.persistence.models import ArtifactRow, RunRow, SessionRow, TurnRow
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
    status: Literal["running", "completed", "failed", "cancelled", "timeout", "budget_exhausted"]
    cancel_requested: bool = False
    committed: CommittedTurn | None = None
    checkpoint_version: int | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    user_turn_id: UUID | None = None


@dataclass(slots=True)
class _SessionState:
    access: TurnAccess
    history: list[HistoryMessage] = field(default_factory=list)
    checkpoint_version: int = 0
    status: Literal["active", "archived"] = "active"


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
        history: SessionHistory = SessionHistory(),
        checkpoint_version: int = 0,
        status: Literal["active", "archived"] = "active",
    ) -> None:
        async with self._lock:
            self._sessions[session_id] = _SessionState(
                access=access,
                history=list(history.messages),
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
                if prior.input_fingerprint != request.input.fingerprint:
                    raise TurnIdempotencyMismatchError("idempotency key is bound to different input")
                if prior.status == "running":
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
            if any(run.session_id == request.session_id and run.status == "running" for run in self._runs.values()):
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
            run.committed = committed
            run.checkpoint_version = session.checkpoint_version
            refs = tuple(item.ref for item in artifacts)
            run.artifacts = refs
            return CommittedTurnReceipt(run.run_id, session.checkpoint_version, committed, refs)

    async def turn_records(
        self,
        session_id: UUID,
        access: TurnAccess,
    ) -> tuple[UserTurnRecord | AssistantTurnRecord, ...]:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.access != access:
                raise TurnNotFoundError("Turn not found")
            completed = sorted(
                (run for run in self._runs.values() if run.session_id == session_id and run.status == "completed"),
                key=lambda run: run.checkpoint_version or 0,
            )
            records: list[UserTurnRecord | AssistantTurnRecord] = []
            for index, run in enumerate(completed):
                if run.user_turn_id is None or run.committed is None:
                    raise TurnStateError("completed Run is incomplete")
                records.extend(
                    (
                        UserTurnRecord(
                            run.user_turn_id,
                            session_id,
                            index * 2 + 1,
                            run.input,
                            run.run_id,
                        ),
                        AssistantTurnRecord(
                            run.run_id,
                            session_id,
                            index * 2 + 2,
                            run.committed,
                            run.run_id,
                        ),
                    )
                )
            return tuple(records)

    async def fail(self, turn: ExecuteTurn, failure: TurnFailure) -> FailedRunReceipt:
        async with self._lock:
            run = self._runs.get(turn.run_id)
            if run is None or run.access != turn.access or run.session_id != turn.session_id:
                raise TurnNotFoundError("Turn not found")
            if run.status == "completed":
                raise TurnStateError("a committed Run cannot be failed")
            if run.claim != turn._claim:
                raise TurnStateError("Turn claim is invalid")
            if run.status == "running":
                run.status = failure.terminal_status
            return FailedRunReceipt(run.run_id, failure.terminal_status, failure.public_message, True)

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

    async def heartbeat(self, turn: ExecuteTurn) -> None:
        async with self._lock:
            self._claimed(turn)

    def _claimed(self, turn: ExecuteTurn) -> tuple[_RunState, _SessionState]:
        run = self._runs.get(turn.run_id)
        session = self._sessions.get(turn.session_id)
        if run is None or session is None or run.access != turn.access or session.access != turn.access:
            raise TurnNotFoundError("Turn not found")
        if run.status != "running" or run.claim != turn._claim:
            raise TurnStateError("Turn is not held by this claim")
        return run, session


class SqlAlchemyTurnStateStore:
    """Transaction-backed authoritative Turn lifecycle state."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        stale_after_seconds: int = 60,
    ) -> None:
        self._sessions = session_factory
        self._stale_after = stale_after_seconds

    async def begin(self, request: BeginTurn) -> TurnStart:
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
            active_run = await db.scalar(
                select(RunRow)
                .where(RunRow.session_id == request.session_id, RunRow.status == "running")
                .with_for_update()
            )
            cutoff = datetime.now(UTC) - timedelta(seconds=self._stale_after)
            heartbeat_at = active_run.claim_heartbeat_at if active_run is not None else None
            if heartbeat_at is not None and heartbeat_at.tzinfo is None:
                heartbeat_at = heartbeat_at.replace(tzinfo=UTC)
            if active_run is not None and heartbeat_at is not None and heartbeat_at < cutoff:
                active_run.status = "failed"
                active_run.failure_code = "stale_claim"
                active_run.failure_public_message = "Turn failed"
                active_run.finished_at = datetime.now(UTC)
                active_run.claim_owner = None
                active_run.claim_heartbeat_at = None
                await db.flush()
            prior = await db.scalar(
                select(RunRow)
                .where(
                    RunRow.session_id == request.session_id,
                    RunRow.idempotency_key == request.idempotency_key,
                    RunRow.status.in_(("running", "completed")),
                )
                .order_by(RunRow.created_at.desc())
                .limit(1)
            )
            if prior is not None:
                if prior.input_fingerprint != request.input.fingerprint:
                    raise TurnIdempotencyMismatchError("idempotency key is bound to different input")
                if prior.status == "running":
                    raise TurnInProgressError("Turn is already running")
                return await self._replay(db, prior)
            active = await db.scalar(
                select(RunRow.id).where(
                    RunRow.session_id == request.session_id,
                    RunRow.status == "running",
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

        async def cancelled() -> bool:
            async with self._sessions() as probe_db:
                value = await probe_db.scalar(
                    select(RunRow.cancel_requested_at).where(RunRow.id == request.proposed_run_id)
                )
                return value is not None

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
            return CommittedTurnReceipt(
                turn.run_id,
                session.checkpoint_version,
                committed,
                tuple(item.ref for item in artifacts),
            )

    async def fail(self, turn: ExecuteTurn, failure: TurnFailure) -> FailedRunReceipt:
        async with self._sessions() as db, db.begin():
            run = await db.get(RunRow, turn.run_id, with_for_update=True)
            if run is None or run.session_id != turn.session_id:
                raise TurnNotFoundError("Turn not found")
            if run.status == "completed":
                raise TurnStateError("a committed Run cannot be failed")
            if run.claim_owner != str(turn._claim.value):
                raise TurnStateError("Turn claim is invalid")
            if run.status == "running":
                run.status = failure.terminal_status
                run.failure_code = failure.terminal_status
                run.failure_public_message = failure.public_message
                run.failure_usage_json = dict(failure.usage)
                run.finished_at = datetime.now(UTC)
                run.claim_owner = None
                run.claim_heartbeat_at = None
            return FailedRunReceipt(run.id, failure.terminal_status, failure.public_message, True)

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

    async def heartbeat(self, turn: ExecuteTurn) -> None:
        async with self._sessions() as db, db.begin():
            run = await db.get(RunRow, turn.run_id, with_for_update=True)
            if (
                run is None
                or run.session_id != turn.session_id
                or run.status != "running"
                or run.claim_owner != str(turn._claim.value)
            ):
                raise TurnStateError("Turn claim is invalid")
            run.claim_heartbeat_at = datetime.now(UTC)

    async def _replay(self, db: AsyncSession, run: RunRow) -> ReplayTurn:
        receipt = await self._receipt(db, run)
        return ReplayTurn(run.id, run.session_id, receipt.committed_turn, receipt.checkpoint_version)

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
