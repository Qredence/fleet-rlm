"""Host-mediated evidence persistence for sandbox RLM loops.

Provides ``store_evidence``, ``fetch_evidence``, and ``list_evidence``
functions that sandbox code calls through the Daytona bridge.  Each
function delegates to the host-side ``FleetRepository`` using the
``_host_repository`` and ``_host_identity`` attributes attached to the
interpreter by the websocket session layer.

This keeps ``DATABASE_URL`` out of the sandbox while giving RLM child
runs durable cross-child evidence sharing via NeonDB.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _host_refs(interpreter: Any) -> tuple[Any, Any, Any]:
    repository = getattr(interpreter, "_host_repository", None)
    identity = getattr(interpreter, "_host_identity", None)
    run_id = getattr(interpreter, "_host_run_id", None)
    return repository, identity, run_id


def store_evidence(
    interpreter: Any,
    key: str,
    content: str,
    kind: str = "context",
    scope: str = "run",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Persist evidence from sandbox code into NeonDB via the host repository."""
    repository, identity, run_id = _host_refs(interpreter)
    if repository is None or identity is None:
        return {"status": "skipped", "reason": "no_repository"}

    from fleet_rlm.integrations.database.models_enums import (
        MemoryKind,
        MemoryScope,
        MemorySource,
    )
    from fleet_rlm.integrations.database.repository_memory import (
        MemoryItemCreateRequest,
    )

    try:
        item = asyncio.run(
            repository.store_memory_item(
                MemoryItemCreateRequest(
                    tenant_id=identity.tenant_id,
                    workspace_id=identity.workspace_id,
                    user_id=identity.user_id,
                    run_id=run_id,
                    scope=MemoryScope(scope),
                    scope_id=str(key),
                    kind=MemoryKind(kind),
                    source=MemorySource.TOOL,
                    content_text=str(content),
                    tags=list(tags or []),
                )
            )
        )
    except Exception as exc:
        logger.warning("store_evidence failed: %s", exc)
        return {"status": "error", "error": str(exc)}
    return {"status": "ok", "id": str(item.id), "key": key}


def fetch_evidence(
    interpreter: Any,
    scope: str = "run",
    scope_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Fetch evidence items from NeonDB for use inside sandbox code."""
    repository, identity, _ = _host_refs(interpreter)
    if repository is None or identity is None:
        return {"status": "skipped", "items": []}

    from fleet_rlm.integrations.database.models_enums import MemoryScope

    try:
        items = asyncio.run(
            repository.list_memory_items(
                tenant_id=identity.tenant_id,
                workspace_id=identity.workspace_id,
                user_id=identity.user_id,
                scope=MemoryScope(scope),
                scope_id=scope_id,
                limit=limit,
            )
        )
    except Exception as exc:
        logger.warning("fetch_evidence failed: %s", exc)
        return {"status": "error", "items": [], "error": str(exc)}
    return {
        "status": "ok",
        "items": [
            {
                "id": str(i.id),
                "scope_id": i.scope_id,
                "content": i.content_text,
                "kind": str(i.kind.value),
            }
            for i in items
        ],
    }


def list_evidence(
    interpreter: Any,
    scope: str = "run",
    limit: int = 50,
) -> dict[str, Any]:
    """List available evidence handles (metadata only, no full content)."""
    repository, identity, _ = _host_refs(interpreter)
    if repository is None or identity is None:
        return {"status": "skipped", "items": []}

    from fleet_rlm.integrations.database.models_enums import MemoryScope

    try:
        items = asyncio.run(
            repository.list_memory_items(
                tenant_id=identity.tenant_id,
                workspace_id=identity.workspace_id,
                scope=MemoryScope(scope),
                limit=limit,
            )
        )
    except Exception as exc:
        logger.warning("list_evidence failed: %s", exc)
        return {"status": "error", "items": [], "error": str(exc)}
    return {
        "status": "ok",
        "items": [
            {
                "id": str(i.id),
                "scope_id": i.scope_id,
                "kind": str(i.kind.value),
                "importance": i.importance,
            }
            for i in items
        ],
    }


_EVIDENCE_TOOL_NAMES = frozenset({"store_evidence", "fetch_evidence", "list_evidence"})

__all__ = [
    "fetch_evidence",
    "list_evidence",
    "store_evidence",
    "_EVIDENCE_TOOL_NAMES",
]
