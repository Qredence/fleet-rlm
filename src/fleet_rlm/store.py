"""The Store: every SQL statement Fleet runs, over one psycopg connection pool.

Methods take the Session ID explicitly. Workspace files live only on the Session's Daytona Volume;
Postgres holds Sessions, Turns, memories and selected replay snapshots. Replay reviews validate
immutable evidence before writing. Pooled connections are autocommit; schema bootstrap and Turn
commits use explicit transactions.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import time
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any, ParamSpec, Self, TypeVar

import psycopg
import psycopg_pool
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from fleet_rlm.optimization_runs import validate_run_id
from fleet_rlm.workspace import validate_session_id

if TYPE_CHECKING:
    pass


class IdempotencyConflictError(ValueError):
    """An idempotency key was reused for a different task; safe to show the caller."""


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_sessions (
        session_id TEXT PRIMARY KEY,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        memory_summary TEXT NOT NULL DEFAULT '',
        turns JSONB NOT NULL DEFAULT '[]'::jsonb
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_vfs_files (
        session_id TEXT NOT NULL REFERENCES fleet_lite_sessions(session_id) ON DELETE CASCADE,
        rel_path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        content BYTEA NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (session_id, rel_path)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_memories (
        session_id TEXT NOT NULL REFERENCES fleet_lite_sessions(session_id) ON DELETE CASCADE,
        memory_id TEXT NOT NULL,
        content TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'observation',
        sources JSONB NOT NULL DEFAULT '{}'::jsonb,
        reviewed BOOLEAN NOT NULL DEFAULT FALSE,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (session_id, memory_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_turns (
        session_id TEXT NOT NULL REFERENCES fleet_lite_sessions(session_id) ON DELETE CASCADE,
        turn_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        task_sha256 TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running',
        result JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY(session_id,turn_id),
        UNIQUE(session_id,idempotency_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_replay_snapshots (
        session_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        content BYTEA NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (session_id, turn_id),
        FOREIGN KEY (session_id, turn_id)
            REFERENCES fleet_lite_turns(session_id, turn_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_runtime_settings (
        session_id TEXT PRIMARY KEY REFERENCES fleet_lite_sessions(session_id) ON DELETE CASCADE,
        settings JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
)
_SCHEMA_VERSION_TABLE = """
    CREATE TABLE IF NOT EXISTS fleet_lite_schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
"""
_REVIEW_LABELS_TABLE = """
    CREATE TABLE IF NOT EXISTS fleet_lite_review_labels (
        session_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        task TEXT NOT NULL,
        workspace_manifest TEXT NOT NULL,
        context_files JSONB NOT NULL,
        review_status TEXT NOT NULL,
        source_group TEXT NOT NULL,
        expected_answer TEXT,
        expected_values JSONB NOT NULL DEFAULT '[]'::jsonb,
        route TEXT,
        critical BOOLEAN NOT NULL DEFAULT FALSE,
        reviewer TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (session_id, turn_id),
        FOREIGN KEY (session_id, turn_id)
            REFERENCES fleet_lite_replay_snapshots(session_id, turn_id) ON DELETE CASCADE
    )
"""
_OPTIMIZATION_RUNS_TABLE = """
    CREATE TABLE IF NOT EXISTS fleet_lite_optimization_runs (
        session_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        options JSONB NOT NULL DEFAULT '{}'::jsonb,
        owner_id TEXT NOT NULL,
        managed BOOLEAN NOT NULL DEFAULT FALSE,
        status TEXT NOT NULL,
        stage TEXT NOT NULL,
        stop_requested BOOLEAN NOT NULL DEFAULT FALSE,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (session_id, run_id),
        UNIQUE (session_id, request_id)
    )
"""
_OPTIMIZATION_RUNS_ACTIVE_INDEX = """
    CREATE UNIQUE INDEX IF NOT EXISTS fleet_lite_one_active_optimization_run
    ON fleet_lite_optimization_runs ((1))
    WHERE status IN ('running', 'approving')
"""

_APPEND_TURN = """
    UPDATE fleet_lite_sessions
    SET turns = (
            SELECT COALESCE(jsonb_agg(entry ORDER BY ordinal ASC), '[]'::jsonb)
            FROM (
                SELECT entry, ordinal
                FROM jsonb_array_elements(turns || %s::jsonb) WITH ORDINALITY AS history(entry, ordinal)
                ORDER BY ordinal DESC
                LIMIT %s
            ) AS bounded_history
        ),
        memory_summary = CASE WHEN %s <> '' THEN
            right(trim(both E'\\n' from (memory_summary || E'\\n' || %s)), 20000)
            ELSE memory_summary END,
        updated_at = NOW()
    WHERE session_id = %s
      AND (%s::text IS NULL OR NOT EXISTS (
          SELECT 1 FROM jsonb_array_elements(turns) AS existing(entry)
          WHERE entry->>'turn_id' = %s
      ))
"""

# Each migration is one atomic schema revision. Add new numbered entries here rather than
# editing an already-shipped migration; the ledger makes upgrades and retries explicit.
_SCHEMA_MIGRATIONS = (
    (1, SCHEMA_STATEMENTS),
    (2, (_REVIEW_LABELS_TABLE,)),
    (
        3,
        (
            "ALTER TABLE fleet_lite_review_labels ADD COLUMN IF NOT EXISTS "
            "structured_expectations JSONB NOT NULL DEFAULT '{}'::jsonb",
        ),
    ),
    # The Daytona Volume is the only workspace file store. Rows are kept for manual recovery.
    (4, ("ALTER TABLE IF EXISTS fleet_lite_vfs_files RENAME TO fleet_lite_vfs_files_retired",)),
    # The environment observation is a durable Session fact. Releasing or rebuilding a runtime
    # must not re-emit it, so the marker lives on the Session row; existing rows start unreported.
    (
        5,
        (
            "ALTER TABLE fleet_lite_sessions ADD COLUMN IF NOT EXISTS "
            "environment_reported BOOLEAN NOT NULL DEFAULT FALSE",
        ),
    ),
    (6, (_OPTIMIZATION_RUNS_TABLE, _OPTIMIZATION_RUNS_ACTIVE_INDEX)),
    (
        7,
        (
            "CREATE INDEX IF NOT EXISTS fleet_lite_memories_content_fts "
            "ON fleet_lite_memories USING GIN (to_tsvector('english', content))",
        ),
    ),
    (
        8,
        ("ALTER TABLE fleet_lite_sessions ADD COLUMN IF NOT EXISTS sandbox_ownership JSONB",),
    ),
)
SCHEMA_VERSION = _SCHEMA_MIGRATIONS[-1][0]
_REPLAY_RETENTION_DAYS = 30
_RETRY_DELAY_SECONDS = 0.5
# Lakebase credentials live at most one hour; recycle pooled connections before theirs expire.
_POOL_MAX_LIFETIME_SECONDS = 50 * 60.0
_POOL_MAX_IDLE_SECONDS = 10 * 60.0
_POOL_RECONNECT_TIMEOUT_SECONDS = 60.0
_OPTIMIZATION_LOCK_SCOPE = "fleet:optimization-global-lease"


_P = ParamSpec("_P")
_R = TypeVar("_R")


def _retry_once(method: Callable[_P, _R]) -> Callable[_P, _R]:
    """Retry once on a connection error. Lakebase closes idle connections; the pool discards the dead one.

    Safe because every Store write is an upsert, a conditional update, or a delete; `commit_turn` is
    conditional on the record's turn_id, which it therefore requires.
    """

    @functools.wraps(method)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            return method(*args, **kwargs)
        except psycopg.OperationalError:
            time.sleep(_RETRY_DELAY_SECONDS)
            return method(*args, **kwargs)

    return wrapper


class Store:
    """Pooled access to the Lite tables."""

    def __init__(
        self,
        pool: ConnectionPool,
        dsn: str,
        connection_class: type[psycopg.Connection[Any]] = psycopg.Connection,
        *,
        migration_pool: ConnectionPool | None = None,
        schema: str = "public",
        connection_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Wrap a runtime pool with an optional DDL pool; the schema must be a safe identifier."""
        self.pool = pool
        self.dsn = dsn
        self.connection_class = connection_class
        self.migration_pool = migration_pool or pool
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
            raise ValueError("database schema must be a lowercase PostgreSQL identifier")
        self.schema = schema
        self.connection_kwargs = connection_kwargs or {}

    @classmethod
    def open(
        cls,
        dsn: str,
        *,
        wait: bool = False,
        max_size: int = 10,
        lakebase_endpoint: str = "",
        migration_dsn: str = "",
        schema: str = "public",
    ) -> Store:
        """Open a pool. With wait=False the (slow, cold) first connect happens in the background."""
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
            raise ValueError("database schema must be a lowercase PostgreSQL identifier")
        connection_class: type[psycopg.Connection[Any]] = psycopg.Connection
        if lakebase_endpoint:
            from databricks.sdk import WorkspaceClient

            connection_class = lakebase_connection_class(WorkspaceClient(), lakebase_endpoint)
        connection_kwargs: dict[str, Any] = {"connect_timeout": 30, "autocommit": True}
        if schema != "public":
            connection_kwargs["options"] = f"-c search_path={schema}"
        pool_options: dict[str, Any] = {
            "kwargs": connection_kwargs,
            "connection_class": connection_class,
            "max_lifetime": _POOL_MAX_LIFETIME_SECONDS,
            "max_idle": _POOL_MAX_IDLE_SECONDS,
            "reconnect_timeout": _POOL_RECONNECT_TIMEOUT_SECONDS,
            # A connection Lakebase closed while idle is discarded on checkout rather than handed out.
            "check": psycopg_pool.ConnectionPool.check_connection,
        }
        pool = ConnectionPool(dsn, min_size=1, max_size=max_size, open=False, name="fleet", **pool_options)
        pool.open(wait=wait, timeout=60)
        migration_pool = None
        try:
            if migration_dsn and migration_dsn != dsn:
                migration_pool = ConnectionPool(
                    migration_dsn, min_size=1, max_size=1, open=False, name="fleet-migrations", **pool_options
                )
                migration_pool.open(wait=wait, timeout=60)
        except BaseException:
            pool.close()
            raise
        return cls(
            pool,
            dsn,
            connection_class,
            migration_pool=migration_pool,
            schema=schema,
            connection_kwargs=connection_kwargs,
        )

    def close(self) -> None:
        """Release the DDL pool, then the runtime pool, even if the first close fails."""
        try:
            self.close_migration_pool()
        finally:
            self.pool.close()

    def close_migration_pool(self) -> None:
        """Release the optional DDL role connection pool after startup migrations."""
        migration_pool = self.migration_pool
        if migration_pool is not self.pool:
            self.migration_pool = self.pool
            migration_pool.close()

    # Schema and Sessions

    @_retry_once
    def initialize(self) -> None:
        """Apply each pending schema migration once under a transaction-scoped lock."""
        with self.migration_pool.connection() as connection, connection.transaction():
            if self.schema != "public":
                connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
                connection.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.schema)))
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,17))", ("fleet_lite_schema_migrations",)
            )
            connection.execute(_SCHEMA_VERSION_TABLE)
            row = connection.execute("SELECT MAX(version) FROM fleet_lite_schema_migrations").fetchone()
            current_version = int(row[0]) if row and row[0] is not None else 0
            if current_version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {current_version} is newer than this Fleet version {SCHEMA_VERSION}"
                )
            for version, statements in _SCHEMA_MIGRATIONS:
                if version <= current_version:
                    continue
                if version != current_version + 1:
                    raise RuntimeError(f"Missing Fleet schema migration {current_version + 1}")
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO fleet_lite_schema_migrations(version) VALUES (%s) ON CONFLICT DO NOTHING",
                    (version,),
                )
                current_version = version
            connection.execute(
                "DELETE FROM fleet_lite_replay_snapshots WHERE created_at < NOW() - (%s * INTERVAL '1 day')",
                (_REPLAY_RETENTION_DAYS,),
            )

    @_retry_once
    def ensure_session(self, session_id: str) -> None:
        """Create a new Session with known-empty ownership; legacy rows remain unknown."""
        with self.pool.connection() as connection:
            connection.execute(
                "INSERT INTO fleet_lite_sessions (session_id,sandbox_ownership) "
                "VALUES (%s,%s::jsonb) ON CONFLICT DO NOTHING",
                (session_id, json.dumps({"epoch": 0, "resources": []})),
            )

    @_retry_once
    def advance_sandbox_ownership_epoch(self, session_id: str) -> int | None:
        """Advance the current owner epoch, or return None when legacy provenance is unknown."""
        with self.pool.connection() as connection, connection.transaction():
            row = connection.execute(
                "UPDATE fleet_lite_sessions SET sandbox_ownership = "
                "jsonb_set(sandbox_ownership, '{epoch}', to_jsonb((sandbox_ownership->>'epoch')::bigint + 1)) "
                "WHERE session_id=%s AND sandbox_ownership IS NOT NULL RETURNING sandbox_ownership->>'epoch'",
                (session_id,),
            ).fetchone()
        return int(row[0]) if row else None

    @_retry_once
    def sandbox_ownership(self, session_id: str) -> dict[str, Any] | None:
        """Read bounded sandbox ownership intent; None means the Session predates ownership tracking."""
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT sandbox_ownership FROM fleet_lite_sessions WHERE session_id=%s", (session_id,)
            ).fetchone()
        if row is None or row[0] is None:
            return None
        value = row[0]
        if isinstance(value, str):
            value = json.loads(value)
        if (
            not isinstance(value, dict)
            or type(value.get("epoch")) is not int
            or not isinstance(value.get("resources"), list)
            or len(value["resources"]) > 32
        ):
            raise RuntimeError("Stored sandbox ownership record is invalid")
        return value

    @_retry_once
    def mutate_sandbox_resource(
        self, session_id: str, epoch: int, agent_id: str, resource: dict[str, Any] | None
    ) -> bool:
        """Atomically upsert or remove one bounded resource under its current ownership epoch."""
        if not agent_id or len(agent_id) > 64:
            raise ValueError("Invalid sandbox agent identity")
        encoded = json.dumps(resource, separators=(",", ":")) if resource is not None else None
        if encoded is not None and len(encoded) > 2_000:
            raise ValueError("Sandbox resource intent exceeds its size limit")
        with self.pool.connection() as connection, connection.transaction():
            cursor = connection.cursor(row_factory=dict_row)
            row = cursor.execute(
                "SELECT sandbox_ownership FROM fleet_lite_sessions WHERE session_id=%s FOR UPDATE",
                (session_id,),
            ).fetchone()
            ownership = (row or {}).get("sandbox_ownership")
            if isinstance(ownership, str):
                ownership = json.loads(ownership)
            if not isinstance(ownership, dict) or ownership.get("epoch") != epoch:
                return False
            resources = ownership.get("resources")
            if not isinstance(resources, list):
                raise RuntimeError("Stored sandbox ownership record is invalid")
            resources = [entry for entry in resources if entry.get("agent_id") != agent_id]
            if resource is not None:
                if len(resources) >= 32:
                    raise RuntimeError("Session sandbox ownership resource limit reached")
                resources.append({"agent_id": agent_id, **resource})
            ownership["resources"] = resources
            cursor.execute(
                "UPDATE fleet_lite_sessions SET sandbox_ownership=%s::jsonb WHERE session_id=%s "
                "AND sandbox_ownership->>'epoch'=%s",
                (json.dumps(ownership, separators=(",", ":")), session_id, str(epoch)),
            )
        return True

    @_retry_once
    def claim_environment_report(self, session_id: str) -> bool:
        """Atomically claim this Session's environment observation.

        Return True only when an existing unreported Session is updated; an unknown Session
        or prior claim returns False. Database errors propagate.
        """
        with self.pool.connection() as connection:
            claimed = connection.execute(
                "UPDATE fleet_lite_sessions SET environment_reported = TRUE "
                "WHERE session_id = %s AND environment_reported = FALSE RETURNING session_id",
                (session_id,),
            ).fetchone()
        return claimed is not None

    @_retry_once
    def list_sessions(self) -> list[dict[str, Any]]:
        """Return the 100 most recently updated Sessions with their Turn counts."""
        with self.pool.connection() as connection:
            return (
                connection.cursor(row_factory=dict_row)
                .execute(
                    "SELECT session_id, updated_at, jsonb_array_length(turns) AS turn_count "
                    "FROM fleet_lite_sessions ORDER BY updated_at DESC LIMIT 100"
                )
                .fetchall()
            )

    @_retry_once
    def history(self, session_id: str) -> list[dict[str, Any]]:
        """Return the stored Turn history for a Session, or an empty list if none exists."""
        with self.pool.connection() as connection:
            row = connection.execute("SELECT turns FROM fleet_lite_sessions WHERE session_id = %s", (session_id,))
            found = row.fetchone()
        return found[0] if found else []

    def acquire_session_lease(self, session_id: str) -> psycopg.Connection:
        """A dedicated (unpooled) connection holding the Session's advisory lock until it is closed."""
        connection = self.connection_class.connect(
            self.dsn,
            **self.connection_kwargs,
        )
        try:
            locked = connection.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s,17))", (session_id,)
            ).fetchone()
            if not locked or not locked[0]:
                raise RuntimeError("Session is owned by another worker")
            return connection
        except BaseException:
            connection.close()
            raise

    # Runtime settings

    @_retry_once
    def runtime_settings(self, session_id: str) -> dict[str, Any]:
        """Return the Session's saved runtime settings, or an empty dict."""
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT settings FROM fleet_lite_runtime_settings WHERE session_id=%s", (session_id,)
            ).fetchone()
        return row[0] if row else {}

    @_retry_once
    def save_runtime_settings(self, session_id: str, settings: dict[str, Any]) -> None:
        """Merge settings into the Session's saved runtime settings."""
        with self.pool.connection() as connection:
            connection.execute(
                "INSERT INTO fleet_lite_runtime_settings(session_id,settings) VALUES(%s,%s::jsonb) "
                "ON CONFLICT(session_id) DO UPDATE "
                "SET settings=fleet_lite_runtime_settings.settings || EXCLUDED.settings",
                (session_id, json.dumps(settings)),
            )

    # Memories

    @_retry_once
    def save_memory(
        self, session_id: str, memory_id: str, content: str, kind: str, sources: dict[str, str], reviewed: bool
    ) -> None:
        """Insert or replace one memory row, keyed by Session and memory ID."""
        with self.pool.connection() as connection:
            connection.execute(
                "INSERT INTO fleet_lite_memories(session_id,memory_id,content,kind,sources,reviewed) "
                "VALUES (%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT(session_id,memory_id) DO UPDATE "
                "SET content=EXCLUDED.content,kind=EXCLUDED.kind,sources=EXCLUDED.sources,"
                "reviewed=EXCLUDED.reviewed,updated_at=NOW()",
                (session_id, memory_id, content, kind, json.dumps(sources), reviewed),
            )

    @_retry_once
    def memories(self, session_id: str, search: str, *, reviewed_only: bool) -> list[dict[str, Any]]:
        """Memories matching a full-text search, best match first."""
        with self.pool.connection() as connection:
            cursor = connection.cursor(row_factory=dict_row)
            rows = cursor.execute(
                "SELECT memory_id,content,kind,sources,reviewed,updated_at FROM fleet_lite_memories "
                "WHERE session_id=%s AND (%s=FALSE OR reviewed=TRUE) "
                "AND (%s='' OR to_tsvector('english',content) @@ websearch_to_tsquery('english',%s)) "
                "ORDER BY ts_rank_cd(to_tsvector('english',content),websearch_to_tsquery('english',%s)) DESC, "
                "updated_at DESC LIMIT 20",
                (session_id, reviewed_only, search, search, search),
            ).fetchall()
        for row in rows:
            row["updated_at"] = row["updated_at"].isoformat()
        return rows

    @_retry_once
    def delete_memory(self, session_id: str, memory_id: str) -> None:
        """Delete one memory row from a Session."""
        with self.pool.connection() as connection:
            connection.execute(
                "DELETE FROM fleet_lite_memories WHERE session_id=%s AND memory_id=%s", (session_id, memory_id)
            )

    # Turns

    @_retry_once
    def claim_turn(
        self,
        session_id: str,
        turn_id: str,
        key: str,
        task_sha256: str,
        *,
        ownership_epoch: int | None = None,
    ) -> dict[str, Any] | None:
        """Record intent; None when newly claimed, else the prior attempt with the same key."""
        with self.pool.connection() as connection, connection.transaction():
            if ownership_epoch is not None:
                self._assert_ownership_epoch(connection, session_id, ownership_epoch)
            inserted = connection.execute(
                "INSERT INTO fleet_lite_turns(session_id,turn_id,idempotency_key,task_sha256) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT(session_id,idempotency_key) DO NOTHING RETURNING turn_id",
                (session_id, turn_id, key, task_sha256),
            ).fetchone()
            if inserted:
                return None
            prior = (
                connection.cursor(row_factory=dict_row)
                .execute(
                    "SELECT turn_id,task_sha256,status,result FROM fleet_lite_turns "
                    "WHERE session_id=%s AND idempotency_key=%s",
                    (session_id, key),
                )
                .fetchone()
            )
        if prior is None:
            raise RuntimeError("Turn intent disappeared during claim")
        return prior

    @_retry_once
    def finish_turn(
        self,
        session_id: str,
        turn_id: str,
        status: str,
        result: dict[str, Any],
        *,
        ownership_epoch: int | None = None,
    ) -> None:
        """Set a Turn's terminal status and result unless it is already completed."""
        with self.pool.connection() as connection:
            if ownership_epoch is not None:
                with connection.transaction():
                    self._assert_ownership_epoch(connection, session_id, ownership_epoch)
                    connection.execute(
                        "UPDATE fleet_lite_turns SET status=%s,result=%s::jsonb,updated_at=NOW() "
                        "WHERE session_id=%s AND turn_id=%s AND status <> 'completed'",
                        (status, json.dumps(result), session_id, turn_id),
                    )
                return
            connection.execute(
                "UPDATE fleet_lite_turns SET status=%s,result=%s::jsonb,updated_at=NOW() "
                "WHERE session_id=%s AND turn_id=%s AND status <> 'completed'",
                (status, json.dumps(result), session_id, turn_id),
            )

    @staticmethod
    def _assert_ownership_epoch(connection: Any, session_id: str, ownership_epoch: int) -> None:
        row = connection.execute(
            "SELECT sandbox_ownership->>'epoch' FROM fleet_lite_sessions WHERE session_id=%s FOR UPDATE",
            (session_id,),
        ).fetchone()
        current = int(row[0]) if row and row[0] is not None else None
        if current != ownership_epoch:
            raise RuntimeError("Session ownership epoch changed; stale Turn write was fenced")

    @_retry_once
    def fail_interrupted_turns(self, session_id: str, ownership_epoch: int) -> list[str]:
        """Terminalize prior running Turns after a new lease owner reconciles their sandbox resources."""
        result = {
            "event": "error",
            "outcome": "failed",
            "error": "Turn interrupted by process restart; generated code was not replayed.",
        }
        with self.pool.connection() as connection, connection.transaction():
            self._assert_ownership_epoch(connection, session_id, ownership_epoch)
            rows = connection.execute(
                "UPDATE fleet_lite_turns SET status='failed',result=%s::jsonb,updated_at=NOW() "
                "WHERE session_id=%s AND status='running' RETURNING turn_id",
                (json.dumps(result), session_id),
            ).fetchall()
        return [str(row[0]) for row in rows]

    @_retry_once
    def turn_status(self, session_id: str, turn_id: str) -> dict[str, Any] | None:
        """Return one Turn's status, result, and update time, or None if absent."""
        with self.pool.connection() as connection:
            row = (
                connection.cursor(row_factory=dict_row)
                .execute(
                    "SELECT turn_id,status,result,updated_at FROM fleet_lite_turns WHERE session_id=%s AND turn_id=%s",
                    (session_id, turn_id),
                )
                .fetchone()
            )
        if row:
            row["updated_at"] = row["updated_at"].isoformat()
        return row

    # Session context and Turn commits

    @_retry_once
    def load_session(self, session_id: str) -> tuple[str, list[Any]]:
        """Return context for a Session row created before its runtime was published."""
        with self.pool.connection() as connection:
            session = (
                connection.cursor(row_factory=dict_row)
                .execute("SELECT memory_summary, turns FROM fleet_lite_sessions WHERE session_id = %s", (session_id,))
                .fetchone()
            )
        return str((session or {}).get("memory_summary") or ""), (session or {}).get("turns") or []

    @_retry_once
    def commit_turn(
        self,
        session_id: str,
        *,
        turn_record_json: str,
        max_turns: int = 200,
        memory_note: str = "",
        completion: tuple[str, dict[str, Any]] | None = None,
        replay_snapshot: tuple[str, bytes] | None = None,
        ownership_epoch: int | None = None,
    ) -> None:
        """In one transaction: append the Turn record and memory note, store a selected replay, complete the Turn.

        `completion` is (turn_id, result) for the turn record.
        """
        if replay_snapshot is not None and completion is None:
            raise ValueError("A replay snapshot can only be committed with a completed Turn")
        turn_id = None
        with suppress(TypeError, AttributeError, json.JSONDecodeError):
            turn_id = json.loads(turn_record_json).get("turn_id")
        if not turn_id:
            # The append is guarded on turn_id; without one, the connection retry could store the Turn twice.
            raise ValueError("Turn record must carry a turn_id")
        with self.pool.connection() as connection, connection.transaction():
            connection.execute(
                "INSERT INTO fleet_lite_sessions (session_id) VALUES (%s) ON CONFLICT DO NOTHING", (session_id,)
            )
            connection.execute(
                "SELECT session_id FROM fleet_lite_sessions WHERE session_id = %s FOR UPDATE", (session_id,)
            )
            if ownership_epoch is not None:
                self._assert_ownership_epoch(connection, session_id, ownership_epoch)
            note = memory_note[:10_000]
            connection.execute(_APPEND_TURN, (turn_record_json, max_turns, note, note, session_id, turn_id, turn_id))
            if replay_snapshot is not None:
                assert completion is not None
                digest, content = replay_snapshot
                if len(content) > 16_000_000 or hashlib.sha256(content).hexdigest() != digest:
                    raise ValueError("Replay snapshot digest or size validation failed")
                turn_id = completion[0]
                connection.execute(
                    "INSERT INTO fleet_lite_replay_snapshots(session_id,turn_id,sha256,content) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT(session_id,turn_id) DO NOTHING",
                    (session_id, turn_id, digest, content),
                )
                stored_digest = connection.execute(
                    "SELECT sha256 FROM fleet_lite_replay_snapshots WHERE session_id=%s AND turn_id=%s",
                    (session_id, turn_id),
                ).fetchone()
                if not stored_digest or stored_digest[0] != digest:
                    raise ValueError("Turn already has a different immutable replay snapshot")
            if completion is not None:
                turn_id, result = completion
                connection.execute(
                    "UPDATE fleet_lite_turns SET status='completed',result=%s::jsonb,updated_at=NOW() "
                    "WHERE session_id=%s AND turn_id=%s",
                    (json.dumps(result), session_id, turn_id),
                )

    # Optimization examples

    @_retry_once
    def save_review_labels(
        self,
        session_id: str,
        turn_id: str,
        snapshot_sha256: str,
        *,
        review_status: str,
        source_group: str,
        expected_answer: str | None,
        expected_values: list[str],
        route: str | None,
        critical: bool,
        reviewer: str,
        structured_expectations: dict[str, Any] | None = None,
    ) -> None:
        """Persist reviewed labels only when they match an existing immutable replay snapshot."""
        from fleet_rlm.replay import selected_snapshot, validate_review_labels

        validate_review_labels(
            dict(
                review_status=review_status,
                source_group=source_group,
                expected_answer=expected_answer,
                expected_values=expected_values,
                critical=critical,
                structured_expectations=structured_expectations or {},
            ),
            require_gold=True,
        )
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("review labels require a reviewer")
        snapshot = selected_snapshot(self, session_id, turn_id, snapshot_sha256)
        task = snapshot["task"]
        workspace_manifest = snapshot["workspace_manifest"]
        context_files = snapshot["context_files"]
        with self.pool.connection() as connection:
            saved = connection.execute(
                """
                INSERT INTO fleet_lite_review_labels(
                    session_id,turn_id,task,workspace_manifest,context_files,review_status,source_group,
                    expected_answer,expected_values,route,critical,reviewer,structured_expectations
                )
                SELECT %s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb
                WHERE EXISTS (
                    SELECT 1 FROM fleet_lite_replay_snapshots
                    WHERE session_id=%s AND turn_id=%s AND sha256=%s
                )
                ON CONFLICT (session_id,turn_id) DO UPDATE SET
                    task=EXCLUDED.task,
                    workspace_manifest=EXCLUDED.workspace_manifest,
                    context_files=EXCLUDED.context_files,
                    review_status=EXCLUDED.review_status,
                    source_group=EXCLUDED.source_group,
                    expected_answer=EXCLUDED.expected_answer,
                    expected_values=EXCLUDED.expected_values,
                    route=EXCLUDED.route,
                    critical=EXCLUDED.critical,
                    reviewer=EXCLUDED.reviewer,
                    structured_expectations=EXCLUDED.structured_expectations,
                    updated_at=NOW()
                RETURNING 1
                """,
                (
                    session_id,
                    turn_id,
                    task,
                    workspace_manifest,
                    json.dumps(context_files),
                    review_status,
                    source_group,
                    expected_answer,
                    json.dumps(expected_values),
                    route,
                    critical,
                    reviewer,
                    json.dumps(structured_expectations or {}),
                    session_id,
                    turn_id,
                    snapshot_sha256,
                ),
            ).fetchone()
        if not saved:
            raise ValueError("Selected replay snapshot is missing or has changed")

    @_retry_once
    def reviewed_examples(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        """Approved, gold-labeled turns with a complete immutable replay snapshot."""
        with self.pool.connection() as connection:
            return (
                connection.cursor(row_factory=dict_row)
                .execute(
                    """
                    SELECT labels.turn_id,
                           labels.task,
                           labels.workspace_manifest,
                           labels.expected_answer AS answer,
                           labels.expected_values,
                           labels.structured_expectations,
                           labels.route,
                           labels.source_group,
                           labels.critical,
                           labels.context_files,
                           replay.sha256 AS replay_snapshot_sha256,
                           replay.content AS replay_snapshot
                    FROM fleet_lite_review_labels AS labels
                    JOIN fleet_lite_replay_snapshots AS replay
                      ON replay.session_id=labels.session_id AND replay.turn_id=labels.turn_id
                    WHERE labels.session_id = %s
                      AND labels.review_status = 'approved'
                      AND (NULLIF(labels.expected_answer,'') IS NOT NULL
                           OR jsonb_array_length(labels.expected_values) > 0)
                    ORDER BY labels.updated_at DESC
                    LIMIT %s
                    """,
                    (session_id, limit),
                )
                .fetchall()
            )

    @_retry_once
    def replay_snapshot(self, session_id: str, turn_id: str) -> tuple[str, bytes] | None:
        """Fetch selected immutable replay evidence by its owning Session and Turn."""
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT sha256,content FROM fleet_lite_replay_snapshots WHERE session_id=%s AND turn_id=%s",
                (session_id, turn_id),
            ).fetchone()
        return (row[0], bytes(row[1])) if row else None

    # Optimization run ledger

    def acquire_optimization_lease(self) -> psycopg.Connection:
        """Hold the one process-wide optimization lease on a dedicated connection."""
        return self._acquire_optimization_lock(_OPTIMIZATION_LOCK_SCOPE)

    def acquire_optimization_owner_lease(self, owner_id: str) -> psycopg.Connection:
        """Keep an API owner live through pending launch, execution, and draining."""
        owner_id = self._optimization_owner_id(owner_id)
        return self._acquire_optimization_lock(f"fleet:optimization-owner:{owner_id}")

    def optimization_owner_alive(self, owner_id: str) -> bool:
        """Probe a separate connection; database errors are not evidence of owner death."""
        try:
            lease = self.acquire_optimization_owner_lease(owner_id)
        except RuntimeError:
            return True
        lease.close()
        return False

    def _acquire_optimization_lock(self, scope: str) -> psycopg.Connection:
        """Acquire a nonblocking advisory lock on a dedicated connection the caller must close.

        Raise RuntimeError if the lock is held. Close the new connection on acquisition
        failure and propagate database errors.
        """
        connection = self.connection_class.connect(self.dsn, **self.connection_kwargs)
        try:
            locked = connection.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,17))", (scope,)).fetchone()
            if not locked or not locked[0]:
                raise RuntimeError("An optimization run is already owned by another worker")
            return connection
        except BaseException:
            connection.close()
            raise

    @_retry_once
    def create_optimization_run(
        self,
        session_id: str,
        run_id: str,
        request_id: str,
        options: dict[str, Any],
        owner_id: str,
        *,
        managed: bool = False,
    ) -> dict[str, Any]:
        """Create one globally active run, or return an identical idempotent request retry."""
        session_id = validate_session_id(session_id)
        run_id = validate_run_id(run_id)
        request_id = self._optimization_request_id(request_id)
        owner_id = self._optimization_owner_id(owner_id)
        if not isinstance(options, dict):
            raise ValueError("optimization options must be an object")
        if type(managed) is not bool:
            raise ValueError("managed must be a boolean")
        options_json = self._optimization_json(options, "optimization options")
        try:
            with self.pool.connection() as connection, connection.transaction():
                inserted = connection.execute(
                    "INSERT INTO fleet_lite_optimization_runs "
                    "(session_id,run_id,request_id,options,owner_id,managed,status,stage) "
                    "VALUES (%s,%s,%s,%s::jsonb,%s,%s,'running','preparing') "
                    "ON CONFLICT (session_id,request_id) DO NOTHING RETURNING *",
                    (session_id, run_id, request_id, options_json, owner_id, managed),
                ).fetchone()
                if inserted is not None:
                    return self._optimization_run_record(inserted)
                prior = connection.execute(
                    "SELECT * FROM fleet_lite_optimization_runs WHERE session_id=%s AND request_id=%s FOR UPDATE",
                    (session_id, request_id),
                ).fetchone()
                if prior is None:
                    raise ValueError("optimization run request conflicts with an existing run")
                record = self._optimization_run_record(prior)
                if (
                    record["options"] != json.loads(options_json)
                    or record["owner_id"] != owner_id
                    or record["managed"] is not managed
                ):
                    raise ValueError("optimization request_id was already used with different run details")
                return record
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError("An optimization run is already active or the run identity is in use") from exc

    @_retry_once
    def get_optimization_run(self, session_id: str, run_id: str) -> dict[str, Any] | None:
        """Return one run from the durable ledger, or None when the Session/run pair is absent."""
        session_id = validate_session_id(session_id)
        run_id = validate_run_id(run_id)
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT * FROM fleet_lite_optimization_runs WHERE session_id=%s AND run_id=%s",
                (session_id, run_id),
            ).fetchone()
        return self._optimization_run_record(row) if row is not None else None

    @_retry_once
    def list_optimization_runs(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """List optimization history, either for one Session or across all Sessions."""
        if session_id is not None:
            session_id = validate_session_id(session_id)
        with self.pool.connection() as connection:
            if session_id is None:
                rows = connection.execute(
                    "SELECT * FROM fleet_lite_optimization_runs ORDER BY updated_at DESC,run_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM fleet_lite_optimization_runs WHERE session_id=%s ORDER BY updated_at DESC,run_id",
                    (session_id,),
                ).fetchall()
        return [self._optimization_run_record(row) for row in rows]

    @_retry_once
    def update_optimization_run(
        self,
        session_id: str,
        run_id: str,
        updates: dict[str, Any],
        *,
        owner_id: str | None = None,
        expected_statuses: tuple[str, ...] | None = None,
    ) -> dict[str, Any] | None:
        """Apply a metadata/status update only while the optional owner and status fence match.

        Return the updated record, or None for a missing run or failed fence. A completion
        update becomes cancelled if a stop was already accepted, clearing candidate_version.
        Raise ValueError for invalid updates, identity changes, or an active-run conflict;
        other database errors propagate.
        """
        session_id = validate_session_id(session_id)
        run_id = validate_run_id(run_id)
        if not isinstance(updates, dict):
            raise ValueError("optimization updates must be an object")
        forbidden = {"session_id", "run_id", "request_id", "options", "owner_id", "managed", "created_at", "updated_at"}
        if forbidden & updates.keys():
            raise ValueError("optimization run identity fields cannot be changed")
        if "status" in updates and (not isinstance(updates["status"], str) or not updates["status"].strip()):
            raise ValueError("optimization status must be nonempty text")
        if "stage" in updates and (not isinstance(updates["stage"], str) or not updates["stage"].strip()):
            raise ValueError("optimization stage must be nonempty text")
        if "stop_requested" in updates and type(updates["stop_requested"]) is not bool:
            raise ValueError("stop_requested must be a boolean")
        if expected_statuses is not None and (
            not isinstance(expected_statuses, tuple)
            or not expected_statuses
            or any(not isinstance(value, str) or not value for value in expected_statuses)
        ):
            raise ValueError("expected_statuses must be a nonempty tuple of statuses")
        metadata = {key: value for key, value in updates.items() if key not in {"status", "stage", "stop_requested"}}
        metadata_json = self._optimization_json(metadata, "optimization metadata")
        owner_id = self._optimization_owner_id(owner_id) if owner_id is not None else None
        statuses = list(expected_statuses) if expected_statuses is not None else None
        finishing_completed = updates.get("status") == "completed"
        try:
            with self.pool.connection() as connection:
                row = connection.execute(
                    "UPDATE fleet_lite_optimization_runs SET "
                    "status=CASE WHEN %s AND stop_requested THEN 'cancelled' ELSE COALESCE(%s,status) END,"
                    "stage=CASE WHEN %s AND stop_requested THEN 'cancelled' ELSE COALESCE(%s,stage) END,"
                    "stop_requested=COALESCE(%s,stop_requested),"
                    "metadata=metadata || %s::jsonb || CASE WHEN %s AND stop_requested "
                    "THEN '{\"candidate_version\":null}'::jsonb ELSE '{}'::jsonb END,updated_at=NOW() "
                    "WHERE session_id=%s AND run_id=%s "
                    "AND (%s::text IS NULL OR owner_id=%s) "
                    "AND (%s::text[] IS NULL OR status=ANY(%s::text[])) RETURNING *",
                    (
                        finishing_completed,
                        updates.get("status"),
                        finishing_completed,
                        updates.get("stage"),
                        updates.get("stop_requested"),
                        metadata_json,
                        finishing_completed,
                        session_id,
                        run_id,
                        owner_id,
                        owner_id,
                        statuses,
                        statuses,
                    ),
                ).fetchone()
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError("Another optimization run is active") from exc
        return self._optimization_run_record(row) if row is not None else None

    @_retry_once
    def request_optimization_stop(self, session_id: str, run_id: str) -> dict[str, Any]:
        """Set a cooperative stop request for a running optimization; repeated requests are safe."""
        session_id = validate_session_id(session_id)
        run_id = validate_run_id(run_id)
        with self.pool.connection() as connection:
            row = connection.execute(
                "UPDATE fleet_lite_optimization_runs SET stop_requested=TRUE, "
                "updated_at=CASE WHEN stop_requested THEN updated_at ELSE NOW() END "
                "WHERE session_id=%s AND run_id=%s AND status='running' RETURNING *",
                (session_id, run_id),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM fleet_lite_optimization_runs WHERE session_id=%s AND run_id=%s",
                    (session_id, run_id),
                ).fetchone()
        if row is None:
            raise ValueError("optimization run was not found")
        record = self._optimization_run_record(row)
        if record["status"] != "running":
            raise ValueError("only a running optimization can be stopped")
        return record

    @_retry_once
    def claim_optimization_review(self, session_id: str, run_id: str, owner_id: str) -> dict[str, Any]:
        """Claim a completed candidate for approval, or take over its recoverable approving state."""
        session_id = validate_session_id(session_id)
        run_id = validate_run_id(run_id)
        owner_id = self._optimization_owner_id(owner_id)
        try:
            with self.pool.connection() as connection, connection.transaction():
                prior = connection.execute(
                    "SELECT * FROM fleet_lite_optimization_runs WHERE session_id=%s AND run_id=%s FOR UPDATE",
                    (session_id, run_id),
                ).fetchone()
                if prior is None:
                    raise ValueError("optimization run was not found")
                record = self._optimization_run_record(prior)
                if record["status"] not in {"completed", "approving"}:
                    raise ValueError("only a completed or recoverable approving run can be reviewed")
                row = connection.execute(
                    "UPDATE fleet_lite_optimization_runs SET status='approving',owner_id=%s,updated_at=NOW() "
                    "WHERE session_id=%s AND run_id=%s AND status IN ('completed','approving') RETURNING *",
                    (owner_id, session_id, run_id),
                ).fetchone()
                if row is None:
                    raise ValueError("optimization review claim was lost")
                return self._optimization_run_record(row)
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError("Another optimization run is active") from exc

    @_retry_once
    def reject_optimization_run(self, session_id: str, run_id: str) -> dict[str, Any]:
        """Record a rejection for a completed candidate without deleting its artifacts or history."""
        session_id = validate_session_id(session_id)
        run_id = validate_run_id(run_id)
        with self.pool.connection() as connection:
            row = connection.execute(
                "UPDATE fleet_lite_optimization_runs SET status='rejected',"
                "metadata=metadata || CASE WHEN status='rejected' THEN '{}'::jsonb "
                "ELSE jsonb_build_object('decision','rejected','decision_at',NOW()) END,"
                "updated_at=CASE WHEN status='rejected' THEN updated_at ELSE NOW() END "
                "WHERE session_id=%s AND run_id=%s AND status IN ('completed','rejected') RETURNING *",
                (session_id, run_id),
            ).fetchone()
        if row is None:
            raise ValueError("only a completed optimization run can be rejected")
        return self._optimization_run_record(row)

    @staticmethod
    def _optimization_request_id(value: Any) -> str:
        """Return a nonblank request ID of at most 200 characters, or raise ValueError."""
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError("optimization request_id must contain 1-200 characters")
        return value

    @staticmethod
    def _optimization_owner_id(value: Any) -> str:
        """Return a nonblank owner ID of at most 200 characters, or raise ValueError."""
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError("optimization owner_id must contain 1-200 characters")
        return value

    @staticmethod
    def _optimization_json(value: dict[str, Any], label: str) -> str:
        """Serialize finite JSON values, converting serialization failures to a labeled ValueError."""
        try:
            return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must contain JSON values") from exc

    @staticmethod
    def _optimization_run_record(row: Any) -> dict[str, Any]:
        """Merge free-form run details with canonical ledger columns, preserving ledger authority."""
        if isinstance(row, dict):
            values = dict(row)
        else:
            columns = (
                "session_id",
                "run_id",
                "request_id",
                "options",
                "owner_id",
                "managed",
                "status",
                "stage",
                "stop_requested",
                "metadata",
                "created_at",
                "updated_at",
            )
            values = dict(zip(columns, row, strict=False))
        metadata = values.pop("metadata", {}) or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        if not isinstance(metadata, dict):
            raise ValueError("stored optimization metadata is invalid")
        values = {**metadata, **values}
        for name in ("created_at", "updated_at"):
            stamp = values.get(name)
            if hasattr(stamp, "isoformat"):
                values[name] = stamp.isoformat()
        return values


def lakebase_connection_class(client: Any, endpoint: str) -> type[psycopg.Connection[Any]]:
    """Create psycopg connections with a fresh Lakebase OAuth credential per physical connection."""

    class LakebaseOAuthConnection(psycopg.Connection):
        @classmethod
        def connect(
            cls,
            conninfo: str = "",
            *,
            autocommit: bool = False,
            prepare_threshold: int | None = 5,
            context: Any = None,
            row_factory: Any = None,
            cursor_factory: Any = None,
            **kwargs: Any,
        ) -> Self:
            credential = client.postgres.generate_database_credential(endpoint=endpoint)
            kwargs["password"] = credential.token
            return super().connect(
                conninfo,
                autocommit=autocommit,
                prepare_threshold=prepare_threshold,
                context=context,
                row_factory=row_factory,
                cursor_factory=cursor_factory,
                **kwargs,
            )

    return LakebaseOAuthConnection
