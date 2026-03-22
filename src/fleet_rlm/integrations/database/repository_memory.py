"""Memory repository operations."""

from __future__ import annotations

import uuid

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert

from .models import MemoryItem, MemoryKind, MemoryScope, MemorySource
from .repository_shared import RepositoryContextMixin
from .types import MemoryItemCreateRequest


class RepositoryMemoryMixin(RepositoryContextMixin):
    async def store_memory_item(self, request: MemoryItemCreateRequest) -> MemoryItem:
        scope = (
            request.scope
            if isinstance(request.scope, MemoryScope)
            else MemoryScope(request.scope)
        )
        kind = (
            request.kind
            if isinstance(request.kind, MemoryKind)
            else MemoryKind(request.kind)
        )
        source = (
            request.source
            if isinstance(request.source, MemorySource)
            else MemorySource(request.source)
        )
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, request.tenant_id)
            stmt = (
                insert(MemoryItem)
                .values(
                    tenant_id=request.tenant_id,
                    scope=scope,
                    scope_id=request.scope_id,
                    kind=kind,
                    uri=request.uri,
                    content_text=request.content_text,
                    content_json=request.content_json,
                    source=source,
                    importance=request.importance,
                    tags=request.tags,
                )
                .returning(MemoryItem)
            )
            result = await session.execute(stmt)
            return result.scalar_one()

    async def list_memory_items(
        self,
        *,
        tenant_id: uuid.UUID,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        limit: int = 100,
    ) -> list[MemoryItem]:
        async with self._db.session() as session, session.begin():
            await self._set_request_context(session, tenant_id)
            stmt: Select[tuple[MemoryItem]] = select(MemoryItem).where(
                MemoryItem.tenant_id == tenant_id
            )
            if scope is not None:
                stmt = stmt.where(MemoryItem.scope == scope)
            if scope_id is not None:
                stmt = stmt.where(MemoryItem.scope_id == scope_id)
            stmt = stmt.order_by(MemoryItem.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())
