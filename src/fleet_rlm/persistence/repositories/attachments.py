"""SQLAlchemy metadata adapters for Attachments."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.files.errors import AttachmentNotFoundError
from fleet_rlm.files.lifecycle import StoredAttachment
from fleet_rlm.files.models import AttachmentAccess, AttachmentRef
from fleet_rlm.persistence.models import AttachmentRow, UserRow, WorkspaceRow


class SqlAlchemyAttachmentCatalog:
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


__all__ = ["SqlAlchemyAttachmentCatalog", "StoredAttachment"]
