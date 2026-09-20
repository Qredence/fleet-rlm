"""Unified Session and Session-Resource persistence adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.artifacts.errors import ArtifactNotFoundError
from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactRef, CompletedRun
from fleet_rlm.artifacts.reader import StoredArtifact
from fleet_rlm.artifacts.safety import parse_kind
from fleet_rlm.attachments import (
    AttachmentAccess,
    AttachmentNotFoundError,
    AttachmentRef,
    StoredAttachment,
)
from fleet_rlm.paths import DEFAULT_VOLUME_MOUNT_PATH
from fleet_rlm.persistence.models import (
    ArtifactRow,
    AttachmentRow,
    RunRow,
    SandboxBindingRow,
    SessionRow,
    TurnRow,
    UserRow,
    WorkspaceRow,
)
from fleet_rlm.runtime.bindings import SandboxBinding, validate_sandbox_binding
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

if TYPE_CHECKING:
    from fleet_rlm.persistence.repositories.turns import InMemoryRunStateStore


# ---------------------------------------------------------------------------
# Session Catalog Adapters
# ---------------------------------------------------------------------------


def _session_record(row: SessionRow) -> SessionRecord:
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
    """SQL read/write Session Catalog adapter."""

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
            return _session_record(row)

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
            return SessionPage(tuple(_session_record(row) for row in rows), total)

    async def get(self, session_id: UUID, *, user_id: UUID, workspace_id: UUID) -> SessionRecord:
        async with self._sessions() as db:
            row = await self._owned(db, session_id, user_id, workspace_id)
            return _session_record(row)

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
            return _session_record(row)

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


# ---------------------------------------------------------------------------
# Artifact Catalog
# ---------------------------------------------------------------------------


class SqlAlchemyArtifactCatalog:
    """SQLAlchemy committed Artifact catalog adapter."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, *, access: ArtifactAccess, artifact_id: UUID) -> StoredArtifact:
        async with self._session_factory() as db:
            row = await db.get(ArtifactRow, artifact_id)
            if (
                row is None
                or row.user_id != access.user_id
                or row.workspace_id != access.workspace_id
                or row.session_id is None
                or row.run_id is None
                or not row.storage_ref
            ):
                raise ArtifactNotFoundError("artifact not found")
            return StoredArtifact(
                ref=ArtifactRef(
                    id=row.id,
                    session_id=row.session_id,
                    run_id=row.run_id,
                    kind=parse_kind(row.kind),
                    title=row.title,
                    media_type=row.media_type,
                    byte_size=row.byte_size,
                    checksum_sha256=row.checksum_sha256,
                ),
                storage_ref=row.storage_ref,
            )

    async def count(self) -> int:
        async with self._session_factory() as db:
            result = await db.execute(select(func.count()).select_from(ArtifactRow))
            return int(result.scalar_one())

    async def list_storage_refs(self, *, workspace_id: UUID) -> frozenset[str]:
        async with self._session_factory() as db:
            result = await db.execute(
                select(ArtifactRow.storage_ref).where(
                    ArtifactRow.workspace_id == workspace_id,
                    ArtifactRow.storage_ref != "",
                )
            )
            return frozenset(str(value) for value in result.scalars())

    async def list_completed_runs(self, *, workspace_id: UUID) -> frozenset[CompletedRun]:
        async with self._session_factory() as db:
            result = await db.execute(
                select(RunRow.session_id, RunRow.id)
                .join(SessionRow, SessionRow.id == RunRow.session_id)
                .where(
                    SessionRow.workspace_id == workspace_id,
                    RunRow.status == "completed",
                )
            )
            return frozenset(CompletedRun(session_id=session_id, run_id=run_id) for session_id, run_id in result.all())

    async def list_active_runs(self, *, workspace_id: UUID) -> frozenset[CompletedRun]:
        async with self._session_factory() as db:
            result = await db.execute(
                select(RunRow.session_id, RunRow.id)
                .join(SessionRow, SessionRow.id == RunRow.session_id)
                .where(
                    SessionRow.workspace_id == workspace_id,
                    RunRow.status.in_(("running", "settling")),
                )
            )
            return frozenset(CompletedRun(session_id=session_id, run_id=run_id) for session_id, run_id in result.all())


# ---------------------------------------------------------------------------
# Attachment Catalog
# ---------------------------------------------------------------------------


class SqlAlchemyAttachmentCatalog:
    """SQLAlchemy metadata adapter for Attachments."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        ref: AttachmentRef,
        access: AttachmentAccess,
        storage_ref: str,
    ) -> None:
        async with self._session_factory() as db:
            if await db.get(UserRow, access.user_id) is None:
                db.add(UserRow(id=access.user_id))
            if await db.get(WorkspaceRow, access.workspace_id) is None:
                db.add(WorkspaceRow(id=access.workspace_id, name="default"))
            await db.flush()
            db.add(
                AttachmentRow(
                    id=ref.id,
                    workspace_id=access.workspace_id,
                    user_id=access.user_id,
                    filename=ref.filename,
                    content_type=ref.content_type,
                    byte_size=ref.byte_size,
                    checksum_sha256=ref.checksum_sha256,
                    storage_ref=storage_ref,
                )
            )
            await db.commit()
        return None

    async def get_many(
        self,
        *,
        access: AttachmentAccess,
        attachment_ids: Sequence[UUID],
    ) -> tuple[StoredAttachment, ...]:
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(AttachmentRow).where(
                        AttachmentRow.id.in_(attachment_ids),
                        AttachmentRow.user_id == access.user_id,
                        AttachmentRow.workspace_id == access.workspace_id,
                    )
                )
            ).all()
            by_id = {row.id: row for row in rows}
            if len(by_id) != len(attachment_ids):
                raise AttachmentNotFoundError("attachment not found")
            return tuple(
                StoredAttachment(
                    ref=AttachmentRef(
                        id=by_id[item].id,
                        filename=by_id[item].filename,
                        content_type=by_id[item].content_type,
                        byte_size=by_id[item].byte_size,
                        checksum_sha256=by_id[item].checksum_sha256,
                    ),
                    storage_ref=by_id[item].storage_ref,
                )
                for item in attachment_ids
            )

    async def count(self) -> int:
        async with self._session_factory() as db:
            result = await db.execute(select(func.count()).select_from(AttachmentRow))
            return int(result.scalar_one())


# ---------------------------------------------------------------------------
# Sandbox Binding Store
# ---------------------------------------------------------------------------


def _row_to_binding(row: SandboxBindingRow) -> SandboxBinding:
    return SandboxBinding(
        session_id=row.session_id,
        sandbox_id=row.sandbox_id,
        workspace_id=row.workspace_id,
        volume_id=row.volume_id,
        volume_subpath=row.volume_subpath,
        mount_path=row.mount_path,
        provider_state=row.provider_state,
        last_verified_at=row.last_verified_at,
        generation=row.generation,
    )


class SqlAlchemySandboxBindingStore:
    """Persist per-session Sandbox/Volume binding metadata in SQL."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, session_id: UUID) -> SandboxBinding | None:
        async with self._session_factory() as db:
            result = await db.execute(select(SandboxBindingRow).where(SandboxBindingRow.session_id == session_id))
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _row_to_binding(row)

    async def get_scoped(self, session_id: UUID, *, workspace_id: UUID) -> SandboxBinding | None:
        async with self._session_factory() as db:
            result = await db.execute(
                select(SandboxBindingRow).where(
                    SandboxBindingRow.session_id == session_id,
                    SandboxBindingRow.workspace_id == workspace_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _row_to_binding(row)

    async def upsert(self, binding: SandboxBinding) -> SandboxBinding:
        validate_sandbox_binding(binding)
        try:
            return await self._write_binding(binding)
        except IntegrityError:
            return await self._write_binding(binding)

    async def _write_binding(self, binding: SandboxBinding) -> SandboxBinding:
        async with self._session_factory() as db:
            result = await db.execute(
                select(SandboxBindingRow).where(SandboxBindingRow.session_id == binding.session_id).with_for_update()
            )
            row = result.scalar_one_or_none()
            if row is not None and row.workspace_id != binding.workspace_id:
                raise ValueError("sandbox binding workspace scope mismatch")
            if row is not None and binding.generation < row.generation:
                raise ValueError("stale sandbox binding generation")
            if row is not None and binding.generation == row.generation and binding.sandbox_id != row.sandbox_id:
                raise ValueError("conflicting sandbox binding identity for generation")
            if (
                row is not None
                and binding.generation == row.generation
                and binding.sandbox_id == row.sandbox_id
                and row.provider_state != "running"
                and binding.provider_state == "running"
            ):
                raise ValueError("stale running sandbox binding generation")
            now = datetime.now(UTC)
            mount_path = binding.mount_path or DEFAULT_VOLUME_MOUNT_PATH
            if row is None:
                row = SandboxBindingRow(
                    id=uuid4(),
                    session_id=binding.session_id,
                    sandbox_id=binding.sandbox_id,
                    workspace_id=binding.workspace_id,
                    volume_id=binding.volume_id,
                    volume_subpath=binding.volume_subpath,
                    mount_path=mount_path,
                    provider_state=binding.provider_state,
                    last_verified_at=binding.last_verified_at or now,
                    generation=binding.generation,
                )
                db.add(row)
            else:
                row.sandbox_id = binding.sandbox_id
                row.workspace_id = binding.workspace_id
                row.volume_id = binding.volume_id
                row.volume_subpath = binding.volume_subpath
                row.mount_path = mount_path
                row.provider_state = binding.provider_state
                row.last_verified_at = binding.last_verified_at or now
                row.generation = binding.generation
            await db.commit()
            return _row_to_binding(row)

    async def replace_with_next_generation(self, binding: SandboxBinding) -> SandboxBinding:
        validate_sandbox_binding(binding)
        try:
            return await self._replace_with_next_generation(binding)
        except IntegrityError:
            return await self._replace_with_next_generation(binding)

    async def _replace_with_next_generation(self, binding: SandboxBinding) -> SandboxBinding:
        async with self._session_factory() as db:
            result = await db.execute(
                select(SandboxBindingRow).where(SandboxBindingRow.session_id == binding.session_id).with_for_update()
            )
            row = result.scalar_one_or_none()
            if row is not None and row.workspace_id != binding.workspace_id:
                raise ValueError("sandbox binding workspace scope mismatch")
            if row is None:
                generation = binding.generation
            elif row.sandbox_id == binding.sandbox_id and row.provider_state == "running":
                generation = row.generation
            else:
                generation = row.generation + 1
            candidate = replace(binding, generation=generation)
            now = datetime.now(UTC)
            mount_path = candidate.mount_path or DEFAULT_VOLUME_MOUNT_PATH
            if row is None:
                row = SandboxBindingRow(
                    id=uuid4(),
                    session_id=candidate.session_id,
                    sandbox_id=candidate.sandbox_id,
                    workspace_id=candidate.workspace_id,
                    volume_id=candidate.volume_id,
                    volume_subpath=candidate.volume_subpath,
                    mount_path=mount_path,
                    provider_state=candidate.provider_state,
                    last_verified_at=candidate.last_verified_at or now,
                    generation=candidate.generation,
                )
                db.add(row)
            else:
                row.sandbox_id = candidate.sandbox_id
                row.workspace_id = candidate.workspace_id
                row.volume_id = candidate.volume_id
                row.volume_subpath = candidate.volume_subpath
                row.mount_path = mount_path
                row.provider_state = candidate.provider_state
                row.last_verified_at = candidate.last_verified_at or now
                row.generation = candidate.generation
            await db.commit()
            return _row_to_binding(row)


__all__ = [
    "CompletedRun",
    "InMemorySessionCatalog",
    "SandboxBinding",
    "SqlAlchemyArtifactCatalog",
    "SqlAlchemyAttachmentCatalog",
    "SqlAlchemySandboxBindingStore",
    "SqlAlchemySessionCatalog",
    "StoredArtifact",
    "StoredAttachment",
]
