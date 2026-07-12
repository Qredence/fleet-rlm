"""SessionRepository: create/load foundation sessions against clean_* tables."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm_clean.persistence.models import (
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


class SessionRepository:
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
    ) -> TurnClaim:
        """Claim a turn under optional idempotency key; may return a completed replay."""
        key = self._normalize_idempotency_key(idempotency_key)
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
            db.add(
                RunRow(
                    id=rid,
                    session_id=session_id,
                    status="running",
                    idempotency_key=key,
                )
            )
            await db.commit()
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
    ) -> UUID:
        """Record a running turn attempt. Prefer ``claim_turn`` for idempotency."""
        claim = await self.claim_turn(session_id, idempotency_key=idempotency_key, run_id=run_id)
        if claim.replay:
            return claim.run_id
        return claim.run_id

    async def append_completed_exchange(
        self,
        session_id: UUID,
        *,
        user_text: str,
        assistant_text: str,
        run_id: UUID | None = None,
        expected_checkpoint_version: int | None = None,
    ) -> SessionSnapshot:
        """Persist user+assistant completed turns and advance checkpoint_version."""
        async with self._session_factory() as db:
            session = await db.get(SessionRow, session_id)
            if session is None:
                raise SessionNotFoundError(f"session {session_id} not found")

            actual = int(session.checkpoint_version)
            if expected_checkpoint_version is not None and actual != expected_checkpoint_version:
                raise StaleCheckpointError(session_id, expected=expected_checkpoint_version, actual=actual)

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
            new_version = actual + 1
            session.checkpoint_version = new_version
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
