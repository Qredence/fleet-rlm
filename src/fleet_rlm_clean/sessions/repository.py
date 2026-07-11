"""SessionRepository: create/load foundation sessions against clean_* tables."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm_clean.persistence.models import RunRow, SessionRow, TurnRow, UserRow, WorkspaceRow
from fleet_rlm_clean.sessions.errors import SessionNotFoundError
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

    async def begin_run(
        self,
        session_id: UUID,
        *,
        idempotency_key: str | None = None,
        run_id: UUID | None = None,
    ) -> UUID:
        """Record a running turn attempt. Does not append History turns."""
        rid = run_id or uuid4()
        async with self._session_factory() as db:
            session = await db.get(SessionRow, session_id)
            if session is None:
                raise SessionNotFoundError(f"session {session_id} not found")
            db.add(
                RunRow(
                    id=rid,
                    session_id=session_id,
                    status="running",
                    idempotency_key=idempotency_key,
                )
            )
            await db.commit()
            return rid

    async def append_completed_exchange(
        self,
        session_id: UUID,
        *,
        user_text: str,
        assistant_text: str,
        run_id: UUID | None = None,
    ) -> SessionSnapshot:
        """Persist user+assistant completed turns and advance checkpoint_version."""
        async with self._session_factory() as db:
            session = await db.get(SessionRow, session_id)
            if session is None:
                raise SessionNotFoundError(f"session {session_id} not found")

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
            session.checkpoint_version = int(session.checkpoint_version) + 1
            if run_id is not None:
                run = await db.get(RunRow, run_id)
                if run is not None:
                    run.status = "completed"
                    run.finished_at = datetime.now(UTC)
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
