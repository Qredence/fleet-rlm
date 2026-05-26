"""SQLite memory DB initialization for the Daytona persistent volume.

Provides a lightweight async helper that ensures ``memories/core.db`` exists
with the canonical schema on the mounted volume.  Called once per sandbox
session from :func:`~fleet_rlm.integrations.daytona.sdk_ops.ensure_daytona_volume_layout`.

The schema is intentionally minimal — see Phase 5 for full migration tooling.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_MEMORY_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS memory (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def init_memory_db(volume_mount_path: str) -> None:
    """Ensure ``memories/core.db`` exists with the canonical schema.

    Parameters
    ----------
    volume_mount_path:
        Absolute path to the mounted persistent volume root
        (e.g. ``/home/daytona/memory``).  The ``memories/`` subdirectory
        must already exist (created by
        :func:`~fleet_rlm.integrations.daytona.sdk_ops.ensure_daytona_volume_layout`).
    """
    memories_dir = Path(volume_mount_path) / "memories"
    db_path = memories_dir / "core.db"

    if db_path.exists():
        logger.debug("memory_db: core.db already present at %s", db_path)
        return

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(_MEMORY_TABLE_DDL)
            conn.commit()
        finally:
            conn.close()
        logger.info("memory_db: initialized core.db at %s", db_path)
    except Exception as exc:
        logger.warning("memory_db: could not initialize core.db at %s: %s", db_path, exc)


async def ainit_memory_db(volume_mount_path: str) -> None:
    """Async wrapper around :func:`init_memory_db` (runs in a thread)."""
    import asyncio

    await asyncio.to_thread(init_memory_db, volume_mount_path)


__all__ = [
    "ainit_memory_db",
    "init_memory_db",
]
