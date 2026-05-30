"""Persistent memory tools backed by the Daytona volume SQLite DB.

Exports ``remember`` and ``recall`` marked with ``@tool_fn`` so that
``discover_tools()`` can collect them.  The live implementations require
the volume mount path to be bound via ``binding.py`` before use.

Depth gate:
    ``remember`` is a no-op when the calling agent is at ``agent_depth > 0``
    (i.e. a recursive child RLM).  ``recall`` works at any depth.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from fleet_rlm.integrations.daytona.memory_db import configure_memory_connection, init_memory_db
from fleet_rlm.runtime.tools._marker import tool_fn

logger = logging.getLogger(__name__)

_UPSERT_SQL = """
INSERT INTO memory (key, value, scope, writer_agent_depth, created_at, updated_at)
VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
ON CONFLICT(key) DO UPDATE SET
    value = excluded.value,
    scope = excluded.scope,
    writer_agent_depth = excluded.writer_agent_depth,
    updated_at = CURRENT_TIMESTAMP
"""

_RECALL_SQL = """
SELECT key, value, scope, writer_agent_depth, created_at, updated_at
FROM memory
WHERE key LIKE ? OR value LIKE ?
ORDER BY updated_at DESC, created_at DESC
LIMIT 50
"""


def _db_path(volume_mount_path: str) -> Path:
    return Path(volume_mount_path) / "memories" / "core.db"


def _remember_impl(key: str, value: str, *, volume_mount_path: str, agent_depth: int = 0) -> dict[str, Any]:
    if agent_depth > 0:
        return {"status": "skipped", "reason": "remember is depth-gated to root agent only"}
    db = _db_path(volume_mount_path)
    if not db.parent.exists():
        return {"status": "error", "reason": f"memories directory not found at {db.parent}"}
    if not os.access(db.parent, os.W_OK):
        return {"status": "error", "reason": f"memories directory not writable at {db.parent}"}
    try:
        init_memory_db(volume_mount_path)
        conn = sqlite3.connect(str(db))
        try:
            configure_memory_connection(conn)
            conn.execute(_UPSERT_SQL, (key, value, "core", agent_depth))
            conn.commit()
        finally:
            conn.close()
        return {"status": "ok", "key": key, "scope": "core", "writer_agent_depth": agent_depth}
    except Exception as exc:
        logger.warning("remember: write failed for key=%r: %s", key, exc)
        return {"status": "error", "reason": str(exc)}


def _recall_impl(query: str, *, volume_mount_path: str) -> dict[str, Any]:
    db = _db_path(volume_mount_path)
    if not db.exists():
        return {"status": "ok", "results": [], "note": "memory DB not yet initialized"}
    try:
        init_memory_db(volume_mount_path)
        pattern = f"%{query}%"
        conn = sqlite3.connect(str(db))
        try:
            configure_memory_connection(conn)
            rows = conn.execute(_RECALL_SQL, (pattern, pattern)).fetchall()
        finally:
            conn.close()
        results = [
            {
                "key": r[0],
                "value": r[1],
                "scope": r[2],
                "writer_agent_depth": r[3],
                "created_at": r[4],
                "updated_at": r[5],
            }
            for r in rows
        ]
        return {"status": "ok", "results": results, "count": len(results)}
    except Exception as exc:
        logger.warning("recall: read failed for query=%r: %s", query, exc)
        return {"status": "error", "reason": str(exc)}


@tool_fn
def remember(key: str, value: str) -> dict[str, Any]:
    """Store a persistent fact in the volume memory DB.

    Only the root agent (depth 0) may write; recursive child RLMs are blocked
    to prevent accidental memory contamination.

    Args:
        key: Unique string key for the fact.
        value: Text value to persist.
    """
    raise RuntimeError(
        "remember requires a bound volume_mount_path. "
        "Obtain a bound tool list via the agent runtime instead of calling directly."
    )


@tool_fn
def recall(query: str) -> dict[str, Any]:
    """Search the persistent volume memory DB.

    Returns matching memory entries whose key or value contains the query
    string.  Works at any agent depth.

    Args:
        query: Substring to search for across all memory keys and values.
    """
    raise RuntimeError(
        "recall requires a bound volume_mount_path. "
        "Obtain a bound tool list via the agent runtime instead of calling directly."
    )


__all__ = [
    "remember",
    "recall",
    "_remember_impl",
    "_recall_impl",
]
