"""SQL read-oriented Session Catalog adapter."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.persistence.models import SessionRow, TurnRow, UserRow, WorkspaceRow
from fleet_rlm.persistence.repositories.turns import InMemoryRunStateStore
from fleet_rlm.sessions.catalog import SequenceCursor, SessionPage, SessionTurnPage
from fleet_rlm.sessions.committed_turn import CommittedTurnCodec
from fleet_rlm.sessions.errors import SessionNotFoundError
from fleet_rlm.sessions.models import (
    AssistantTurnRecord,
    SessionRecord,
    TurnAccess,
    TurnInputCodec,
    UserTurnRecord,
)


def _record(row: SessionRow) -> SessionRecord:
    return SessionRecord(
        row.id,
        row.user_id,
        row.workspace_id,
        row.status,
        row.title,
        row.checkpoint_version,
        row.created_at,
        row.updated_at,
    )


class SqlAlchemySessionCatalog:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        title: str,
    ) -> SessionRecord:
        async with self._sessions() as db, db.begin():
            if await db.get(UserRow, user_id) is None:
                db.add(UserRow(id=user_id))
            if await db.get(WorkspaceRow, workspace_id) is None:
                db.add(WorkspaceRow(id=workspace_id))
            # Two-phase flush: parents first, then the dependent session row,
            # all in one transaction. Defensive against Lakebase re-ping /
            # scale-to-zero reconnection — guarantees parents are durable
            # before the child INSERT observes the fleet_sessions FK.
            await db.flush()
            row = SessionRow(
                id=uuid4(),
                user_id=user_id,
                workspace_id=workspace_id,
                title=title,
                status="active",
                checkpoint_version=0,
            )
            db.add(row)
            await db.flush()
            await db.refresh(row)
            return _record(row)

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
        filters = [SessionRow.user_id == user_id, SessionRow.workspace_id == workspace_id]
        if status is not None:
            filters.append(SessionRow.status == status)
        if search:
            filters.append(SessionRow.title.ilike(f"%{search}%"))
        async with self._sessions() as db:
            total = int(await db.scalar(select(func.count()).select_from(SessionRow).where(*filters)) or 0)
            rows = (
                await db.scalars(
                    select(SessionRow)
                    .where(*filters)
                    .order_by(SessionRow.updated_at.desc(), SessionRow.id)
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            return SessionPage(tuple(_record(row) for row in rows), total)

    async def get(self, session_id: UUID, *, user_id: UUID, workspace_id: UUID) -> SessionRecord:
        async with self._sessions() as db:
            row = await self._owned(db, session_id, user_id, workspace_id)
            return _record(row)

    async def update(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        title: str | None,
        status: str | None,
    ) -> SessionRecord:
        async with self._sessions() as db, db.begin():
            row = await self._owned(db, session_id, user_id, workspace_id, lock=True)
            if title is not None:
                row.title = title
            if status is not None:
                row.status = status
            await db.flush()
            await db.refresh(row)
            return _record(row)

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
        async with self._sessions() as db:
            await self._owned(db, session_id, user_id, workspace_id)
            query = select(TurnRow).where(TurnRow.session_id == session_id)
            if cursor.after_sequence is not None:
                query = query.where(TurnRow.sequence > cursor.after_sequence)
            rows = (await db.scalars(query.order_by(TurnRow.sequence).limit(limit + 1))).all()
            has_more = len(rows) > limit
            rows = rows[:limit]
            items: list[UserTurnRecord | AssistantTurnRecord] = []
            for row in rows:
                if row.role == "user" and row.user_input_json is not None:
                    items.append(
                        UserTurnRecord(
                            row.id,
                            row.session_id,
                            row.sequence,
                            TurnInputCodec.decode(row.user_input_json),
                            row.run_id,
                        )
                    )
                elif row.role == "assistant" and row.committed_turn_json is not None:
                    items.append(
                        AssistantTurnRecord(
                            row.id,
                            row.session_id,
                            row.sequence,
                            CommittedTurnCodec.decode(row.committed_turn_json),
                            row.run_id,
                        )
                    )
                else:
                    raise RuntimeError("stored Turn shape is invalid")
            next_cursor = rows[-1].sequence if has_more and rows else None
            return SessionTurnPage(tuple(items), next_cursor)

    @staticmethod
    async def _owned(
        db: AsyncSession,
        session_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
        *,
        lock: bool = False,
    ) -> SessionRow:
        query = select(SessionRow).where(
            SessionRow.id == session_id,
            SessionRow.user_id == user_id,
            SessionRow.workspace_id == workspace_id,
        )
        if lock:
            query = query.with_for_update()
        row = await db.scalar(query)
        if row is None:
            raise SessionNotFoundError("session not found")
        return row


class InMemorySessionCatalog:
    """In-memory CRUD catalog sharing authoritative Turn state registration."""

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
