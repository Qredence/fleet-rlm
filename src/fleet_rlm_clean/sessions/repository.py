"""SessionRepository: create/load foundation sessions against clean_* tables."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm_clean.persistence.models import SessionRow, TurnRow, UserRow, WorkspaceRow
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
