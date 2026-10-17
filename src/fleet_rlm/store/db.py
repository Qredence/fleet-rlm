"""The Store: every SQL statement Fleet runs, over one psycopg connection pool, plus the Session harness.

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
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ParamSpec, Self, TypeVar

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from fleet_rlm.workspace import _workspace_relative_path, validate_session_id

if TYPE_CHECKING:
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager


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
)
SCHEMA_VERSION = _SCHEMA_MIGRATIONS[-1][0]
_REPLAY_RETENTION_DAYS = 30


_P = ParamSpec("_P")
_R = TypeVar("_R")


def _retry_once(method: Callable[_P, _R]) -> Callable[_P, _R]:
    """Retry once on a connection error. Lakebase closes idle connections; the pool discards the dead one.

    Safe because every Store write is an upsert, a conditional update, or a delete.
    """

    @functools.wraps(method)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            return method(*args, **kwargs)
        except psycopg.OperationalError:
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
        pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=max_size,
            open=False,
            kwargs=connection_kwargs,
            connection_class=connection_class,
            name="fleet",
        )
        pool.open(wait=wait, timeout=60)
        migration_pool = None
        try:
            if migration_dsn and migration_dsn != dsn:
                migration_pool = ConnectionPool(
                    migration_dsn,
                    min_size=1,
                    max_size=1,
                    open=False,
                    kwargs=connection_kwargs,
                    connection_class=connection_class,
                    name="fleet-migrations",
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
        with self.pool.connection() as connection:
            connection.execute(
                "INSERT INTO fleet_lite_sessions (session_id) VALUES (%s) ON CONFLICT DO NOTHING", (session_id,)
            )

    @_retry_once
    def list_sessions(self) -> list[dict[str, Any]]:
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
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT settings FROM fleet_lite_runtime_settings WHERE session_id=%s", (session_id,)
            ).fetchone()
        return row[0] if row else {}

    @_retry_once
    def save_runtime_settings(self, session_id: str, settings: dict[str, Any]) -> None:
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
        with self.pool.connection() as connection:
            connection.execute(
                "DELETE FROM fleet_lite_memories WHERE session_id=%s AND memory_id=%s", (session_id, memory_id)
            )

    # Turns

    @_retry_once
    def claim_turn(self, session_id: str, turn_id: str, key: str, task_sha256: str) -> dict[str, Any] | None:
        """Record intent; None when newly claimed, else the prior attempt with the same key."""
        with self.pool.connection() as connection:
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
    def finish_turn(self, session_id: str, turn_id: str, status: str, result: dict[str, Any]) -> None:
        with self.pool.connection() as connection:
            connection.execute(
                "UPDATE fleet_lite_turns SET status=%s,result=%s::jsonb,updated_at=NOW() "
                "WHERE session_id=%s AND turn_id=%s AND status <> 'completed'",
                (status, json.dumps(result), session_id, turn_id),
            )

    @_retry_once
    def turn_status(self, session_id: str, turn_id: str) -> dict[str, Any] | None:
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
        """Create the Session if needed; return its memory summary and Turn history."""
        with self.pool.connection() as connection:
            connection.execute(
                "INSERT INTO fleet_lite_sessions (session_id) VALUES (%s) ON CONFLICT DO NOTHING", (session_id,)
            )
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
    ) -> None:
        """In one transaction: append the Turn record and memory note, store a selected replay, complete the Turn.

        `completion` is (turn_id, result) for the turn record.
        """
        if replay_snapshot is not None and completion is None:
            raise ValueError("A replay snapshot can only be committed with a completed Turn")
        with self.pool.connection() as connection, connection.transaction():
            connection.execute(
                "INSERT INTO fleet_lite_sessions (session_id) VALUES (%s) ON CONFLICT DO NOTHING", (session_id,)
            )
            connection.execute(
                "SELECT session_id FROM fleet_lite_sessions WHERE session_id = %s FOR UPDATE", (session_id,)
            )
            note = memory_note[:10_000]
            turn_id = None
            with suppress(TypeError, json.JSONDecodeError):
                turn_id = json.loads(turn_record_json).get("turn_id")
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


_MAX_TURN_RECORD_BYTES = 1_000_000
_SESSION_HISTORY_TURNS = 8
_SESSION_HISTORY_CHARS = 16_000
_MAX_STORED_TURNS = 200


@dataclass(slots=True)
class VolumePostgresHarness:
    """A Session's Postgres state (SQL lives in the Store) next to its Volume-backed workspace."""

    store: Store
    sandbox_mgr: DaytonaSandboxManager

    @property
    def session_id(self) -> str:
        return self.sandbox_mgr.session_id

    def ensure_session(self, session_id: str) -> None:
        self.store.ensure_session(validate_session_id(session_id))

    def save_memory(
        self,
        memory_id: str,
        content: str,
        *,
        sources: dict[str, str] | None = None,
        kind: str = "observation",
        reviewed: bool = False,
    ) -> None:
        validate_session_id(memory_id)
        if (
            not content.strip()
            or len(content) > 10000
            or kind not in {"observation", "preference", "hypothesis", "procedure"}
        ):
            raise ValueError("Invalid memory content or kind")
        sources = sources or {}
        if len(sources) > 100 or any(not self._valid_vfs_path(p) for p in sources):
            raise ValueError("Invalid memory sources")
        self.store.save_memory(self.session_id, memory_id, content, kind, sources, reviewed)

    def memories(self, query: str = "", *, reviewed_only: bool = False) -> list[dict[str, Any]]:
        search = " OR ".join(query[:2000].split()[:32])
        return self.store.memories(self.session_id, search, reviewed_only=reviewed_only)

    def delete_memory(self, memory_id: str) -> None:
        self.store.delete_memory(self.session_id, validate_session_id(memory_id))

    def claim_turn(self, turn_id: str, key: str, task: str) -> dict[str, Any] | None:
        """Persist intent before side effects; an uncertain running turn is never replayed."""
        digest = hashlib.sha256(task.encode()).hexdigest()
        prior = self.store.claim_turn(self.session_id, turn_id, key, digest)
        if prior is not None and prior["task_sha256"] != digest:
            raise IdempotencyConflictError("Idempotency key already belongs to another task")
        return prior

    def turn_status(self, turn_id: str) -> dict[str, Any] | None:
        return self.store.turn_status(self.session_id, validate_session_id(turn_id))

    def load_context(self, session_id: str) -> tuple[str, str]:
        """The Session's memory summary and bounded rendered history."""
        summary, turns = self.store.load_session(validate_session_id(session_id))
        return summary, self._render_history(turns)

    @staticmethod
    def _render_history(turns: list[dict[str, Any]]) -> str:
        if not isinstance(turns, list):
            return ""
        entries: list[str] = []
        total_chars = 0
        for turn in reversed(turns[-_SESSION_HISTORY_TURNS:]):
            if not isinstance(turn, dict):
                continue
            task = str(turn.get("task") or "")
            answer = str(turn.get("answer") or "")
            rendered = f"User: {task}\nAssistant: {answer}"
            separator_chars = 2 if entries else 0
            remaining = _SESSION_HISTORY_CHARS - total_chars - separator_chars
            header = "User: \nAssistant: "
            if remaining < len(header) + 2:
                break
            if len(rendered) > remaining:
                task_budget = min(len(task), 4_000, (remaining - len(header)) // 2)
                task_excerpt = task[:task_budget]
                if len(task) > task_budget:
                    task_excerpt += "…"
                answer_budget = remaining - len(header) - len(task_excerpt)
                if len(answer) > answer_budget:
                    answer_excerpt = "…" + answer[-(answer_budget - 1) :] if answer_budget > 1 else "…"
                else:
                    answer_excerpt = answer
                rendered = f"User: {task_excerpt}\nAssistant: {answer_excerpt}"
            entries.append(rendered)
            total_chars += len(entries[-1])
        return "\n\n".join(reversed(entries))

    def commit_turn(
        self,
        session_id: str,
        turn_record: dict[str, Any],
        new_memory_note: str = "",
        replay_snapshot: tuple[str, bytes] | None = None,
    ) -> list[str]:
        """Record a finished Turn; return the workspace artifacts it can report."""
        if bool(turn_record.get("replay_snapshot_selected")) != (replay_snapshot is not None):
            raise ValueError("Selected replay metadata and immutable snapshot must be committed together")
        if replay_snapshot is not None and turn_record.get("replay_snapshot_sha256") != replay_snapshot[0]:
            raise ValueError("Turn replay digest does not match the immutable snapshot")
        session_id = validate_session_id(session_id)
        record_json = json.dumps(turn_record, ensure_ascii=False, separators=(",", ":"))
        if len(record_json.encode("utf-8")) > _MAX_TURN_RECORD_BYTES:
            raise ValueError("Turn record exceeds the persistence size limit")
        paths = self.sandbox_mgr.list_workspace_files()
        if any(not self._valid_vfs_path(path) for path in paths):
            raise ValueError("Workspace listing returned an invalid relative path")
        artifacts = sorted({path for path in paths if path.startswith("artifacts/")})
        completion = None
        if turn_record.get("turn_id"):
            result = {
                "event": "turn_complete",
                "answer": turn_record.get("answer", ""),
                "route": turn_record.get("route", ""),
                "learned_skill_note": turn_record.get("learned_skill_note", ""),
                "artifacts": artifacts,
            }
            if turn_record.get("trace_id"):
                result["trace_id"] = turn_record["trace_id"]
            completion = (turn_record["turn_id"], result)
        self.store.commit_turn(
            session_id,
            turn_record_json=record_json,
            max_turns=_MAX_STORED_TURNS,
            memory_note=new_memory_note,
            completion=completion,
            replay_snapshot=replay_snapshot,
        )
        return artifacts

    @staticmethod
    def _valid_vfs_path(rel_path: str) -> bool:
        try:
            _workspace_relative_path(rel_path)
        except ValueError:
            return False
        return True


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
