"""SQLAlchemy Session repository implementation for clean_* tables."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm_clean.artifacts.models import ArtifactCandidate
from fleet_rlm_clean.persistence.models import (
    ArtifactRow,
    RunRow,
    SessionCheckpointRow,
    SessionRow,
    TurnRow,
    UserRow,
    WorkspaceRow,
)
from fleet_rlm_clean.sessions.checkpoints import StaleCheckpointError, TurnClaim
from fleet_rlm_clean.sessions.errors import IdempotencyConflictError, SessionNotFoundError
from fleet_rlm_clean.sessions.models import SessionRecord, SessionSnapshot, TurnRecord

DEFAULT_WORKER_ID = "worker-local"


def _to_session_record(row: SessionRow) -> SessionRecord:
    return SessionRecord(
        id=row.id,
        user_id=row.user_id,
        workspace_id=row.workspace_id,
        status=row.status,
        title=row.title,
        checkpoint_version=row.checkpoint_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_turn_record(row: TurnRow) -> TurnRecord:
    return TurnRecord(
        id=row.id,
        session_id=row.session_id,
        sequence=row.sequence,
        role=row.role,
        content=row.content,
        status=row.status,
        run_id=row.run_id,
    )


class SqlAlchemySessionRepository:
    """Async repository over foundation session tables."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def ensure_identity(self, *, user_id: UUID, workspace_id: UUID) -> None:
        """Idempotently insert user and workspace rows when missing."""
        async with self._session_factory() as db:
            if (await db.get(UserRow, user_id)) is None:
                db.add(UserRow(id=user_id))
            if (await db.get(WorkspaceRow, workspace_id)) is None:
                db.add(WorkspaceRow(id=workspace_id, name="default"))
            await db.commit()

    async def create(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        title: str = "New Session",
        session_id: UUID | None = None,
    ) -> SessionRecord:
        await self.ensure_identity(user_id=user_id, workspace_id=workspace_id)
        sid = session_id or uuid4()
        async with self._session_factory() as db:
            row = SessionRow(
                id=sid,
                user_id=user_id,
                workspace_id=workspace_id,
                title=title,
                status="active",
                checkpoint_version=0,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return _to_session_record(row)

    async def load(self, session_id: UUID) -> SessionSnapshot:
        async with self._session_factory() as db:
            return await self._load_in_session(db, session_id)

    async def get_owned(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> SessionRecord:
        """Return session metadata if owned; raise SessionNotFoundError otherwise."""
        async with self._session_factory() as db:
            row = await db.get(SessionRow, session_id)
            if row is None or row.user_id != user_id or row.workspace_id != workspace_id:
                raise SessionNotFoundError(f"session {session_id} not found")
            return _to_session_record(row)

    async def list(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        status: str | None = None,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[tuple[SessionRecord, ...], int]:
        """List sessions for one principal. Ordered by updated_at descending."""
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        async with self._session_factory() as db:
            filters = [
                SessionRow.user_id == user_id,
                SessionRow.workspace_id == workspace_id,
            ]
            if status is not None and status.strip():
                filters.append(SessionRow.status == status.strip())
            if search is not None and search.strip():
                # Portable contains match (SQLite + Postgres)
                filters.append(SessionRow.title.ilike(f"%{search.strip()}%"))

            count_stmt = select(func.count()).select_from(SessionRow).where(*filters)
            total = int((await db.execute(count_stmt)).scalar_one())

            stmt = (
                select(SessionRow)
                .where(*filters)
                .order_by(SessionRow.updated_at.desc(), SessionRow.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            rows = (await db.execute(stmt)).scalars().all()
            return tuple(_to_session_record(r) for r in rows), total

    async def update(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        title: str | None = None,
        status: str | None = None,
    ) -> SessionRecord:
        """Patch title and/or status for an owned session."""
        async with self._session_factory() as db:
            row = await db.get(SessionRow, session_id)
            if row is None or row.user_id != user_id or row.workspace_id != workspace_id:
                raise SessionNotFoundError(f"session {session_id} not found")
            if title is not None:
                cleaned = title.strip()
                if not cleaned:
                    msg = "title must not be empty"
                    raise ValueError(msg)
                row.title = cleaned[:255]
            if status is not None:
                normalized = status.strip().lower()
                if normalized not in {"active", "archived"}:
                    msg = f"invalid session status: {status!r}"
                    raise ValueError(msg)
                row.status = normalized
            row.updated_at = datetime.now(UTC)
            await db.commit()
            await db.refresh(row)
            return _to_session_record(row)

    async def archive(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> SessionRecord:
        """Soft-delete: set status=archived."""
        return await self.update(
            session_id,
            user_id=user_id,
            workspace_id=workspace_id,
            status="archived",
        )

    async def list_turns(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[TurnRecord, ...], int]:
        """Paginated completed turns for an owned session (sequence ascending)."""
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        async with self._session_factory() as db:
            session = await db.get(SessionRow, session_id)
            if session is None or session.user_id != user_id or session.workspace_id != workspace_id:
                raise SessionNotFoundError(f"session {session_id} not found")

            turn_filters = [
                TurnRow.session_id == session_id,
                TurnRow.status == "completed",
            ]
            count_stmt = select(func.count()).select_from(TurnRow).where(*turn_filters)
            total = int((await db.execute(count_stmt)).scalar_one())

            stmt = select(TurnRow).where(*turn_filters).order_by(TurnRow.sequence.asc()).limit(limit).offset(offset)
            turns = tuple(_to_turn_record(t) for t in (await db.execute(stmt)).scalars().all())
            return turns, total

    async def turn_count(self, session_id: UUID) -> int:
        """Count completed turns for a session (caller must already own)."""
        async with self._session_factory() as db:
            result = await db.execute(
                select(func.count())
                .select_from(TurnRow)
                .where(TurnRow.session_id == session_id)
                .where(TurnRow.status == "completed")
            )
            return int(result.scalar_one())

    async def _load_in_session(self, db: AsyncSession, session_id: UUID) -> SessionSnapshot:
        row = await db.get(SessionRow, session_id)
        if row is None:
            raise SessionNotFoundError(f"session {session_id} not found")
        result = await db.execute(
            select(TurnRow)
            .where(TurnRow.session_id == session_id)
            .where(TurnRow.status == "completed")
            .order_by(TurnRow.sequence.asc())
        )
        turns = tuple(_to_turn_record(t) for t in result.scalars().all())
        return SessionSnapshot(session=_to_session_record(row), turns=turns)

    async def _next_sequence(self, db: AsyncSession, session_id: UUID) -> int:
        result = await db.execute(
            select(func.coalesce(func.max(TurnRow.sequence), 0)).where(TurnRow.session_id == session_id)
        )
        current = int(result.scalar_one())
        return current + 1

    @staticmethod
    def _normalize_idempotency_key(key: str | None) -> str | None:
        if key is None:
            return None
        cleaned = key.strip()
        return cleaned or None

    async def claim_turn(
        self,
        session_id: UUID,
        *,
        idempotency_key: str | None = None,
        run_id: UUID | None = None,
        lease_owner: str | None = None,
    ) -> TurnClaim:
        """Claim a turn under optional idempotency key; may return a completed replay."""
        key = self._normalize_idempotency_key(idempotency_key)
        owner = (lease_owner or DEFAULT_WORKER_ID).strip() or DEFAULT_WORKER_ID
        async with self._session_factory() as db:
            session = await db.get(SessionRow, session_id)
            if session is None:
                raise SessionNotFoundError(f"session {session_id} not found")
            base_version = int(session.checkpoint_version)

            if key is not None:
                result = await db.execute(
                    select(RunRow).where(
                        RunRow.session_id == session_id,
                        RunRow.idempotency_key == key,
                    )
                )
                existing = result.scalar_one_or_none()
                if existing is not None:
                    if existing.status == "completed":
                        return TurnClaim(
                            run_id=existing.id,
                            base_checkpoint_version=base_version,
                            replay=True,
                            assistant_text=existing.result_assistant_text,
                        )
                    if existing.status == "running":
                        raise IdempotencyConflictError(
                            f"idempotency key {key!r} already in-flight for session {session_id}"
                        )
                    # Failed prior attempt: allow retry with a new run row by clearing key
                    # uniqueness — re-use same key only after marking failed rows' key null.
                    existing.idempotency_key = None
                    await db.flush()

            rid = run_id or uuid4()
            now = datetime.now(UTC)
            db.add(
                RunRow(
                    id=rid,
                    session_id=session_id,
                    status="running",
                    idempotency_key=key,
                    lease_owner=owner,
                    lease_heartbeat_at=now,
                )
            )
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                if key is None:
                    raise IdempotencyConflictError(f"concurrent run claim failed for session {session_id}") from exc
                result = await db.execute(
                    select(RunRow).where(
                        RunRow.session_id == session_id,
                        RunRow.idempotency_key == key,
                    )
                )
                winner = result.scalar_one_or_none()
                if winner is None:
                    raise IdempotencyConflictError(
                        f"idempotency key {key!r} conflict for session {session_id}"
                    ) from exc
                if winner.status == "completed":
                    return TurnClaim(
                        run_id=winner.id,
                        base_checkpoint_version=base_version,
                        replay=True,
                        assistant_text=winner.result_assistant_text,
                    )
                raise IdempotencyConflictError(
                    f"idempotency key {key!r} already in-flight for session {session_id}"
                ) from exc
            return TurnClaim(
                run_id=rid,
                base_checkpoint_version=base_version,
                replay=False,
            )

    async def begin_run(
        self,
        session_id: UUID,
        *,
        idempotency_key: str | None = None,
        run_id: UUID | None = None,
        lease_owner: str | None = None,
    ) -> UUID:
        """Record a running turn attempt. Prefer ``claim_turn`` for idempotency."""
        claim = await self.claim_turn(
            session_id,
            idempotency_key=idempotency_key,
            run_id=run_id,
            lease_owner=lease_owner,
        )
        if claim.replay:
            return claim.run_id
        return claim.run_id

    async def commit_completed_turn(
        self,
        session_id: UUID,
        *,
        user_text: str,
        assistant_text: str,
        run_id: UUID | None = None,
        expected_checkpoint_version: int | None = None,
        artifact_candidates: tuple[ArtifactCandidate, ...] = (),
    ) -> SessionSnapshot:
        """Atomically persist History, Run/checkpoint, and committed Artifact metadata."""
        async with self._session_factory() as db:
            session = await db.get(SessionRow, session_id)
            if session is None:
                raise SessionNotFoundError(f"session {session_id} not found")

            for candidate in artifact_candidates:
                if (
                    candidate.user_id != session.user_id
                    or candidate.workspace_id != session.workspace_id
                    or candidate.session_id != session_id
                    or candidate.run_id != run_id
                ):
                    raise ValueError("artifact candidate does not belong to this Turn")

            actual = int(session.checkpoint_version)
            expected = actual if expected_checkpoint_version is None else int(expected_checkpoint_version)
            if expected != actual:
                raise StaleCheckpointError(session_id, expected=expected, actual=actual)

            # CAS first so concurrent workers fail closed before mutating turns.
            new_version = expected + 1
            cas = await db.execute(
                update(SessionRow)
                .where(
                    SessionRow.id == session_id,
                    SessionRow.checkpoint_version == expected,
                )
                .values(checkpoint_version=new_version)
            )
            if getattr(cas, "rowcount", 0) != 1:
                refreshed = await db.get(SessionRow, session_id)
                current = int(refreshed.checkpoint_version) if refreshed is not None else -1
                await db.rollback()
                raise StaleCheckpointError(session_id, expected=expected, actual=current)

            seq = await self._next_sequence(db, session_id)
            db.add(
                TurnRow(
                    session_id=session_id,
                    run_id=run_id,
                    sequence=seq,
                    role="user",
                    content=user_text,
                    status="completed",
                )
            )
            db.add(
                TurnRow(
                    session_id=session_id,
                    run_id=run_id,
                    sequence=seq + 1,
                    role="assistant",
                    content=assistant_text,
                    status="completed",
                )
            )
            db.add(
                SessionCheckpointRow(
                    session_id=session_id,
                    version=new_version,
                    payload_json={
                        "run_id": str(run_id) if run_id else None,
                        "user_text_chars": len(user_text),
                        "assistant_text_chars": len(assistant_text),
                    },
                )
            )
            if run_id is not None:
                run = await db.get(RunRow, run_id)
                if run is not None:
                    run.status = "completed"
                    run.finished_at = datetime.now(UTC)
                    run.result_assistant_text = assistant_text
                    run.lease_owner = None
                    run.lease_heartbeat_at = None
            for candidate in artifact_candidates:
                db.add(
                    ArtifactRow(
                        id=candidate.id,
                        workspace_id=candidate.workspace_id,
                        user_id=candidate.user_id,
                        session_id=candidate.session_id,
                        run_id=candidate.run_id,
                        kind=candidate.kind,
                        title=candidate.title,
                        media_type=candidate.media_type,
                        byte_size=candidate.byte_size,
                        checksum_sha256=candidate.checksum_sha256,
                        storage_ref=candidate.durable_path,
                    )
                )
            await db.commit()
        # Fresh session after commit avoids expired attribute lazy-loads.
        return await self.load(session_id)

    async def finish_failed_run(
        self,
        session_id: UUID,
        run_id: UUID,
        *,
        message: str | None = None,
    ) -> SessionSnapshot:
        """Mark run failed without appending History or advancing checkpoint."""
        async with self._session_factory() as db:
            session = await db.get(SessionRow, session_id)
            if session is None:
                raise SessionNotFoundError(f"session {session_id} not found")
            run = await db.get(RunRow, run_id)
            if run is not None and run.session_id == session_id:
                run.status = "failed"
                run.error_message = message
                run.finished_at = datetime.now(UTC)
                run.lease_owner = None
                run.lease_heartbeat_at = None
            await db.commit()
        return await self.load(session_id)

    async def get_owned_session(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> SessionRecord | None:
        """Load a Session owned by the principal; None when missing or foreign."""
        try:
            snap = await self.load(session_id)
        except SessionNotFoundError:
            return None
        if snap.session.user_id != user_id or snap.session.workspace_id != workspace_id:
            return None
        return snap.session

    async def session_run_owned(
        self,
        *,
        session_id: UUID,
        run_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
    ) -> bool:
        """True when Session and Run exist, match each other, and belong to principal."""
        async with self._session_factory() as db:
            session = await db.get(SessionRow, session_id)
            if session is None:
                return False
            if session.user_id != user_id or session.workspace_id != workspace_id:
                return False
            run = await db.get(RunRow, run_id)
            if run is None or run.session_id != session_id:
                return False
            return True

    async def request_cancel(
        self,
        run_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> str:
        """Record durable cancel intent for an owned Run.

        Returns ``cancelled``, ``already_cancelled``, or ``not_found``.
        """
        async with self._session_factory() as db:
            run = await db.get(RunRow, run_id)
            if run is None:
                return "not_found"
            session = await db.get(SessionRow, run.session_id)
            if session is None:
                return "not_found"
            if session.user_id != user_id or session.workspace_id != workspace_id:
                return "not_found"
            if run.status in {"completed", "failed", "cancelled"}:
                return "already_cancelled"
            if run.cancel_requested_at is not None:
                return "already_cancelled"
            run.cancel_requested_at = datetime.now(UTC)
            await db.commit()
            return "cancelled"

    async def is_cancel_requested(self, run_id: UUID) -> bool:
        """True when durable cancel intent is set (holder poll / cross-worker observe)."""
        async with self._session_factory() as db:
            run = await db.get(RunRow, run_id)
            if run is None:
                return False
            return run.cancel_requested_at is not None or run.status == "cancelled"

    async def heartbeat_run_lease(
        self,
        run_id: UUID,
        *,
        lease_owner: str,
    ) -> bool:
        """Refresh lease heartbeat when ``lease_owner`` still holds the Run."""
        owner = lease_owner.strip() or DEFAULT_WORKER_ID
        async with self._session_factory() as db:
            result = await db.execute(
                update(RunRow)
                .where(
                    RunRow.id == run_id,
                    RunRow.lease_owner == owner,
                    RunRow.status == "running",
                )
                .values(lease_heartbeat_at=datetime.now(UTC))
            )
            await db.commit()
            return getattr(result, "rowcount", 0) == 1

    async def reclaim_stale_run_lease(
        self,
        run_id: UUID,
        *,
        lease_owner: str,
        stale_after_seconds: int = 60,
    ) -> bool:
        """Take over a running Run whose heartbeat is older than the stale window."""
        owner = lease_owner.strip() or DEFAULT_WORKER_ID
        cutoff = datetime.now(UTC) - timedelta(seconds=max(1, stale_after_seconds))
        async with self._session_factory() as db:
            run = await db.get(RunRow, run_id)
            if run is None or run.status != "running":
                return False
            if run.lease_owner == owner:
                run.lease_heartbeat_at = datetime.now(UTC)
                await db.commit()
                return True
            heartbeat = run.lease_heartbeat_at
            if heartbeat is not None:
                if heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=UTC)
                if heartbeat > cutoff:
                    return False
            run.lease_owner = owner
            run.lease_heartbeat_at = datetime.now(UTC)
            await db.commit()
            return True
