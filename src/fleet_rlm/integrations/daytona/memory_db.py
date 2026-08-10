"""SQLite memory DB initialization for the Daytona persistent volume.

Provides a lightweight async helper that ensures ``memories/core.db`` exists
with the canonical schema on the mounted volume. Called once per sandbox
session from :func:`~fleet_rlm.integrations.daytona.sdk_ops.ensure_daytona_volume_layout`.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import shutil
import sqlite3
import tempfile
import textwrap
import threading
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

MEMORY_SCHEMA_VERSION = 2

# Per-process record of DBs migrated to the current schema, keyed by absolute
# ``core.db`` path.  The value is a filesystem fingerprint of the DB at the last
# known-current state, so migration runs once per session instead of on every
# read and write.  Guarded by ``_MIGRATION_STATE_LOCK`` for thread safety.
_MIGRATION_STATE_LOCK = threading.Lock()
_migrated_fingerprints: dict[str, tuple[int, int]] = {}

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


@contextlib.contextmanager
def memory_db_lock(db_path: str | Path) -> Iterator[None]:
    """Hold an exclusive advisory lock while touching ``core.db``.

    Uses a ``<db_path>.lock`` sidecar so the lock survives atomic swaps of the
    DB file itself.  Mirrors the ``flock`` seam in
    :mod:`fleet_rlm.integrations.daytona.sandbox_executor` so every cross-process
    memory mutation is serialized.
    """
    lock_path = f"{db_path}.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _db_fingerprint(db_path: Path) -> tuple[int, int] | None:
    try:
        stat = db_path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def note_memory_db_written(db_path: str | Path) -> None:
    """Refresh the session migration fingerprint after an in-place write.

    A write changes the DB mtime, so without this a following read would treat
    the schema as stale and re-run migration.  The caller must already hold
    :func:`memory_db_lock`.
    """
    fingerprint = _db_fingerprint(Path(db_path))
    if fingerprint is None:
        return
    with _MIGRATION_STATE_LOCK:
        _migrated_fingerprints[str(db_path)] = fingerprint


def _migrate_locked(db_path: Path) -> None:
    """Migrate ``core.db`` in place, staging via a same-directory temp file.

    The staged copy is swapped in with :func:`os.replace`, an atomic rename on
    the volume filesystem, so a concurrent reader never sees a half-written
    file.  The temp file must live in the memories directory (not ``/tmp``) so
    the rename stays on one filesystem.  The caller must hold
    :func:`memory_db_lock`.
    """
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=".core.db.", suffix=".tmp", dir=str(db_path.parent))
    os.close(tmp_fd)
    tmp_db = Path(tmp_name)
    try:
        if db_path.exists():
            shutil.copyfile(db_path, tmp_db)
        conn = sqlite3.connect(str(tmp_db))
        try:
            apply_memory_migrations(conn)
        finally:
            conn.close()
        os.replace(tmp_db, db_path)
    finally:
        tmp_db.unlink(missing_ok=True)


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
        with memory_db_lock(db_path):
            _migrate_locked(db_path)
        note_memory_db_written(db_path)
        logger.info("memory_db: initialized/migrated core.db at %s", db_path)
    except Exception as exc:
        logger.warning("memory_db: could not initialize core.db at %s: %s", db_path, exc)


def ensure_memory_db(volume_mount_path: str) -> Path | None:
    """Migrate ``core.db`` once per session and return its path.

    Skips the migration when this process already migrated the DB and its
    fingerprint is unchanged, so ``remember`` and ``recall`` no longer pay the
    copy-out/migrate/copy-back cost on every call.  Returns ``None`` when the
    memories directory is absent.
    """
    memories_dir = Path(volume_mount_path) / "memories"
    if not memories_dir.exists():
        return None

    db_path = memories_dir / "core.db"
    fingerprint = _db_fingerprint(db_path)
    with _MIGRATION_STATE_LOCK:
        cached = _migrated_fingerprints.get(str(db_path))
    if fingerprint is not None and cached == fingerprint:
        return db_path

    with memory_db_lock(db_path):
        _migrate_locked(db_path)
    note_memory_db_written(db_path)
    return db_path


def reset_memory_db_session_cache() -> None:
    """Forget which DBs were migrated this session (for tests)."""
    with _MIGRATION_STATE_LOCK:
        _migrated_fingerprints.clear()


def memory_db_bootstrap_script(mounted_root: str) -> str:
    """Return a remote Python bootstrap script using the canonical migrations."""
    return textwrap.dedent(
        f"""
        from pathlib import Path
        import fcntl
        import os
        import shutil
        import sqlite3
        import tempfile

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
        lock_fd = os.open(str(db_path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            tmp_fd, tmp_name = tempfile.mkstemp(prefix=".core.db.", suffix=".tmp", dir=str(memories_dir))
            os.close(tmp_fd)
            tmp_db = Path(tmp_name)
            try:
                if db_path.exists():
                    shutil.copyfile(db_path, tmp_db)
                conn = sqlite3.connect(str(tmp_db))
                try:
                    apply_migrations(conn)
                finally:
                    conn.close()
                os.replace(tmp_db, db_path)
            finally:
                tmp_db.unlink(missing_ok=True)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
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
    "ensure_memory_db",
    "init_memory_db",
    "memory_db_bootstrap_script",
    "memory_db_lock",
    "note_memory_db_written",
    "reset_memory_db_session_cache",
]
