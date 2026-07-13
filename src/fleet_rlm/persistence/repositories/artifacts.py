"""SQLAlchemy committed Artifact catalog adapter."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.artifacts.errors import ArtifactNotFoundError
from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactRef
from fleet_rlm.artifacts.reader import StoredArtifact
from fleet_rlm.artifacts.safety import parse_kind
from fleet_rlm.persistence.models import ArtifactRow


class SqlAlchemyArtifactCatalog:
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


__all__ = ["SqlAlchemyArtifactCatalog", "StoredArtifact"]
