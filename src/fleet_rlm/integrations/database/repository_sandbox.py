"""Sandbox-session repository operations."""

from __future__ import annotations

import uuid

from sqlalchemy.dialects.postgresql import insert

from .models import SandboxProvider, SandboxSession, SandboxSessionStatus
from .repository_shared import RepositoryContextMixin, _utc_now


class RepositorySandboxMixin(RepositoryContextMixin):
    async def upsert_sandbox_session(
        self,
        *,
        tenant_id: uuid.UUID,
        provider: SandboxProvider,
        external_id: str,
        created_by_user_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id, created_by_user_id)
            stmt = insert(SandboxSession).values(
                tenant_id=tenant_id,
                created_by_user_id=created_by_user_id,
                provider=provider,
                external_id=external_id,
                status=SandboxSessionStatus.ACTIVE,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    SandboxSession.tenant_id,
                    SandboxSession.provider,
                    SandboxSession.external_id,
                ],
                set_={
                    "created_by_user_id": created_by_user_id,
                    "updated_at": _utc_now(),
                },
            ).returning(SandboxSession.id)
            result = await session.execute(stmt)
            return result.scalar_one()
