"""Memory domain repository: memory items."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Select, and_, select
from sqlalchemy.dialects.postgresql import insert

from .engine import DatabaseManager
from .models_enums import MemoryKind, MemoryScope, MemorySource
from .models_memory import MemoryItem
from .repository_shared import RepositoryContextMixin, _coerce_enum


@dataclass(frozen=True)
class MemoryItemCreateRequest:
    tenant_id: uuid.UUID
    scope: MemoryScope
    scope_id: str
    kind: MemoryKind
    source: MemorySource
    workspace_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    uri: str | None = None
    content_text: str | None = None
    content_json: dict[str, Any] | None = None
    importance: int = 0
    tags: list[str] = field(default_factory=list)
    provenance_json: dict[str, Any] = field(default_factory=dict)


class MemoryRepository(RepositoryContextMixin):
    """Memory item storage and retrieval operations."""

    def __init__(self, database: DatabaseManager) -> None:
        self._db = database

    async def store_memory_item(self, request: MemoryItemCreateRequest) -> MemoryItem:
        scope = _coerce_enum(request.scope, MemoryScope)
        kind = _coerce_enum(request.kind, MemoryKind)
        source = _coerce_enum(request.source, MemorySource)
        async with self._db.session() as session, session.begin():
            workspace_id = await self._resolve_workspace_id_in_session(
                session,
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                workspace_id=request.workspace_id,
            )
            await self._set_request_context(
                session,
                request.tenant_id,
                request.user_id,
                workspace_id,
            )
            stmt = (
                insert(MemoryItem)
                .values(
                    tenant_id=request.tenant_id,
                    workspace_id=workspace_id,
                    user_id=request.user_id,
                    run_id=request.run_id,
                    session_id=request.session_id,
                    scope=scope,
                    scope_id=request.scope_id,
                    kind=kind,
                    uri=request.uri,
                    content_text=request.content_text,
                    content_json=request.content_json,
                    source=source,
                    importance=request.importance,
                    tags=request.tags,
                    provenance_json=request.provenance_json,
                )
                .returning(MemoryItem)
            )
            result = await session.execute(stmt)
            return result.scalar_one()

    async def list_memory_items(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        limit: int = 100,
    ) -> list[MemoryItem]:
        async with self._db.session() as session, session.begin():
            resolved_workspace_id = await self._resolve_workspace_id_in_session(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            await self._set_request_context(
                session, tenant_id, workspace_id=resolved_workspace_id
            )
            stmt: Select[tuple[MemoryItem]] = select(MemoryItem).where(
                and_(
                    MemoryItem.tenant_id == tenant_id,
                    MemoryItem.workspace_id == resolved_workspace_id,
                )
            )
            if scope is not None:
                stmt = stmt.where(MemoryItem.scope == scope)
            if scope_id is not None:
                stmt = stmt.where(MemoryItem.scope_id == scope_id)
            stmt = stmt.order_by(MemoryItem.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())


__all__ = [
    "MemoryItemCreateRequest",
    "MemoryRepository",
]
