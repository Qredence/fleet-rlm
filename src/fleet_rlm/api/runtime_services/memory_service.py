"""Memory service encapsulating memory item browsing."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from fleet_rlm.integrations.database import MemoryScope
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult

from ..schemas.memory import MemoryItemResponse, MemoryListResponse


class MemoryService:
    """Encapsulates memory item listing logic."""

    def __init__(self, persistence: Any) -> None:
        self._persistence = persistence

    async def list_memory_items(
        self,
        *,
        persisted_identity: IdentityUpsertResult,
        scope: str | None = None,
        scope_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> MemoryListResponse:
        """Return memory items for the authenticated user, optionally filtered."""
        scope_filter = None
        if scope is not None:
            try:
                scope_filter = MemoryScope(scope)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid scope: {scope}",
                ) from exc

        items, total = await self._persistence.list_memory_items_paginated(
            tenant_id=persisted_identity.tenant_id,
            workspace_id=persisted_identity.workspace_id,
            user_id=persisted_identity.user_id,
            scope=scope_filter,
            scope_id=scope_id,
            limit=limit,
            offset=offset,
        )

        return MemoryListResponse(
            items=[
                MemoryItemResponse(
                    id=str(item.id),
                    scope=item.scope.value if hasattr(item.scope, "value") else str(item.scope),
                    scope_id=item.scope_id,
                    kind=item.kind.value if hasattr(item.kind, "value") else str(item.kind),
                    source=item.source.value if hasattr(item.source, "value") else str(item.source),
                    status=item.status.value if hasattr(item.status, "value") else str(item.status),
                    content_text=item.content_text,
                    importance=item.importance,
                    tags=list(item.tags) if item.tags is not None else [],
                    created_at=item.created_at.isoformat(),
                )
                for item in items
            ],
            total=total,
            offset=offset,
            limit=limit,
            has_more=offset + len(items) < total,
        )
