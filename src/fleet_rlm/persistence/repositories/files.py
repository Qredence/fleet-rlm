"""SQLAlchemy metadata adapters for Attachments and Artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.artifacts.errors import ArtifactNotFoundError
from fleet_rlm.artifacts.models import ArtifactRef
from fleet_rlm.artifacts.safety import parse_kind
from fleet_rlm.files.errors import AttachmentNotFoundError
from fleet_rlm.files.models import AttachmentRef
from fleet_rlm.persistence.models import ArtifactRow, AttachmentRow, UserRow, WorkspaceRow


@dataclass(frozen=True, slots=True)
class StoredAttachment:
    ref: AttachmentRef
    workspace_id: UUID
    user_id: UUID
    storage_ref: str


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    ref: ArtifactRef
    workspace_id: UUID
    user_id: UUID
    storage_ref: str


class SqlAlchemyAttachmentRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        ref: AttachmentRef,
        user_id: UUID,
        workspace_id: UUID,
        storage_ref: str,
    ) -> AttachmentRef:
        async with self._session_factory() as db:
            if await db.get(UserRow, user_id) is None:
                db.add(UserRow(id=user_id))
            if await db.get(WorkspaceRow, workspace_id) is None:
                db.add(WorkspaceRow(id=workspace_id, name="default"))
            await db.flush()
            db.add(
                AttachmentRow(
                    id=ref.id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    filename=ref.filename,
                    content_type=ref.content_type,
                    byte_size=ref.byte_size,
                    checksum_sha256=ref.checksum_sha256,
                    storage_ref=storage_ref,
                )
            )
            await db.commit()
        return ref

    async def get(self, attachment_id: UUID, *, user_id: UUID, workspace_id: UUID) -> StoredAttachment:
        async with self._session_factory() as db:
            row = await db.get(AttachmentRow, attachment_id)
            if row is None or row.user_id != user_id or row.workspace_id != workspace_id or not row.storage_ref:
                raise AttachmentNotFoundError("attachment not found")
            return StoredAttachment(
                ref=AttachmentRef(
                    id=row.id,
                    filename=row.filename,
                    content_type=row.content_type,
                    byte_size=row.byte_size,
                    checksum_sha256=row.checksum_sha256 or "",
                ),
                workspace_id=row.workspace_id,
                user_id=row.user_id,
                storage_ref=row.storage_ref,
            )

    async def count(self) -> int:
        async with self._session_factory() as db:
            result = await db.execute(select(func.count()).select_from(AttachmentRow))
            return int(result.scalar_one())


class SqlAlchemyArtifactRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, artifact_id: UUID, *, user_id: UUID, workspace_id: UUID) -> StoredArtifact:
        async with self._session_factory() as db:
            row = await db.get(ArtifactRow, artifact_id)
            if row is None or row.user_id != user_id or row.workspace_id != workspace_id or not row.storage_ref:
                raise ArtifactNotFoundError("artifact not found")
            if row.session_id is None or row.run_id is None:
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
                    checksum_sha256=row.checksum_sha256 or "",
                ),
                workspace_id=row.workspace_id,
                user_id=row.user_id,
                storage_ref=row.storage_ref,
            )

    async def count(self) -> int:
        async with self._session_factory() as db:
            result = await db.execute(select(func.count()).select_from(ArtifactRow))
            return int(result.scalar_one())
