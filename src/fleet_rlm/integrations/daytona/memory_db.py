"""SQLite memory DB initialization for the Daytona persistent volume.

Provides a lightweight async helper that ensures ``memories/core.db`` exists
with the canonical schema on the mounted volume. Called once per sandbox
session from :func:`~fleet_rlm.integrations.daytona.sdk_ops.ensure_daytona_volume_layout`.
"""

from __future__ import annotations

import logging
import sqlite3
import textwrap
from pathlib import Path

logger = logging.getLogger(__name__)

MEMORY_SCHEMA_VERSION = 2

_MEMORY_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS memory (
    key                TEXT PRIMARY KEY,
    value              TEXT NOT NULL,
    scope              TEXT NOT NULL DEFAULT 'core',
    writer_agent_depth INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_MEMORY_METADATA_COLUMNS: dict[str, str] = {
    "scope": "scope TEXT NOT NULL DEFAULT 'core'",
    "writer_agent_depth": "writer_agent_depth INTEGER NOT NULL DEFAULT 0",
    "created_at": "created_at TEXT NOT NULL DEFAULT ''",
    "updated_at": "updated_at TEXT NOT NULL DEFAULT ''",
}

_MIGRATION_DESCRIPTIONS: dict[int, str] = {
    1: "create memory key/value table",
    2: "add memory metadata columns and indexes",
}


def configure_memory_connection(conn: sqlite3.Connection) -> None:
    """Apply SQLite runtime settings that are safe on Daytona-mounted volumes."""
    conn.execute("PRAGMA busy_timeout = 5000")


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _ensure_memory_columns(conn: sqlite3.Connection) -> None:
    existing = _table_columns(conn, "memory")
    for column, ddl in _MEMORY_METADATA_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE memory ADD COLUMN {ddl}")
            existing.add(column)
    conn.execute(
        """
        UPDATE memory
        SET updated_at = COALESCE(NULLIF(updated_at, ''), NULLIF(created_at, ''), CURRENT_TIMESTAMP)
        WHERE updated_at = ''
        """
    )


def apply_memory_migrations(conn: sqlite3.Connection) -> None:
    """Apply all idempotent memory DB migrations to an open connection."""
    configure_memory_connection(conn)
    conn.execute(_SCHEMA_MIGRATIONS_DDL)
    conn.execute(_MEMORY_TABLE_DDL)
    _ensure_memory_columns(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory(scope)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_updated_at ON memory(updated_at)")
    conn.execute(f"PRAGMA user_version = {MEMORY_SCHEMA_VERSION}")
    for version, description in _MIGRATION_DESCRIPTIONS.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO schema_migrations(version, description)
            VALUES (?, ?)
            """,
            (version, description),
        )
    conn.commit()


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
    if not memories_dir.exists():
        logger.warning("memory_db: memories directory not found at %s", memories_dir)
        return

    db_path = memories_dir / "core.db"
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            apply_memory_migrations(conn)
        finally:
            conn.close()
        logger.info("memory_db: initialized/migrated core.db at %s", db_path)
    except Exception as exc:
        logger.warning("memory_db: could not initialize core.db at %s: %s", db_path, exc)


def memory_db_bootstrap_script(mounted_root: str) -> str:
    """Return a remote Python bootstrap script using the canonical migrations."""
    return textwrap.dedent(
        f"""
        from pathlib import Path
        import shutil
        import sqlite3

        MEMORY_SCHEMA_VERSION = {MEMORY_SCHEMA_VERSION}
        MEMORY_TABLE_DDL = {_MEMORY_TABLE_DDL!r}
        SCHEMA_MIGRATIONS_DDL = {_SCHEMA_MIGRATIONS_DDL!r}
        MEMORY_METADATA_COLUMNS = {_MEMORY_METADATA_COLUMNS!r}
        MIGRATION_DESCRIPTIONS = {_MIGRATION_DESCRIPTIONS!r}

        def table_columns(conn, table_name):
            return {{str(row[1]) for row in conn.execute(f"PRAGMA table_info({{table_name}})").fetchall()}}

        def apply_migrations(conn):
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute(SCHEMA_MIGRATIONS_DDL)
            conn.execute(MEMORY_TABLE_DDL)
            existing = table_columns(conn, "memory")
            for column, ddl in MEMORY_METADATA_COLUMNS.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE memory ADD COLUMN {{ddl}}")
                    existing.add(column)
            conn.execute(
                '''
                UPDATE memory
                SET updated_at = COALESCE(NULLIF(updated_at, ''), NULLIF(created_at, ''), CURRENT_TIMESTAMP)
                WHERE updated_at = ''
                '''
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory(scope)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_updated_at ON memory(updated_at)")
            conn.execute(f"PRAGMA user_version = {{MEMORY_SCHEMA_VERSION}}")
            for version, description in MIGRATION_DESCRIPTIONS.items():
                conn.execute(
                    '''
                    INSERT OR IGNORE INTO schema_migrations(version, description)
                    VALUES (?, ?)
                    ''',
                    (version, description),
                )
            conn.commit()

        root = Path({mounted_root!r})
        memories_dir = root / "memories"
        memories_dir.mkdir(parents=True, exist_ok=True)
        db_path = memories_dir / "core.db"
        tmp_dir = Path("/tmp/fleet-rlm-memory-bootstrap")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_db = tmp_dir / "core.db"
        if db_path.exists():
            shutil.copyfile(db_path, tmp_db)
        elif tmp_db.exists():
            tmp_db.unlink()
        conn = sqlite3.connect(str(tmp_db))
        try:
            apply_migrations(conn)
        finally:
            conn.close()
        shutil.copyfile(tmp_db, db_path)
        """
    )


async def ainit_memory_db(volume_mount_path: str) -> None:
    """Async wrapper around :func:`init_memory_db` (runs in a thread)."""
    import asyncio

    await asyncio.to_thread(init_memory_db, volume_mount_path)


__all__ = [
    "MEMORY_SCHEMA_VERSION",
    "ainit_memory_db",
    "apply_memory_migrations",
    "configure_memory_connection",
    "init_memory_db",
    "memory_db_bootstrap_script",
]
