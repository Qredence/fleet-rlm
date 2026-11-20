"""Squashed PostgreSQL schema definitions and retry helpers."""

from __future__ import annotations

import functools
import time
from collections.abc import Callable
from typing import Any

import psycopg

SCHEMA_VERSION = 2
_REPLAY_RETENTION_DAYS = 30
_RETRY_DELAY_SECONDS = 0.25
_POOL_MAX_LIFETIME_SECONDS = 2_400
_POOL_MAX_IDLE_SECONDS = 600
_POOL_RECONNECT_TIMEOUT_SECONDS = 300
_OPTIMIZATION_LOCK_SCOPE = "fleet:optimization-global-lease"
JOB_KINDS = ("optimize", "evaluate_rescore", "evaluate_replay", "evaluate_synthetic")

_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_sessions (
        session_id TEXT PRIMARY KEY,
        settings JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(settings) = 'object'),
        memory_summary TEXT NOT NULL DEFAULT '',
        turn_count INTEGER NOT NULL DEFAULT 0,
        environment_reported BOOLEAN NOT NULL DEFAULT FALSE,
        ownership_epoch BIGINT NOT NULL DEFAULT 0,
        lease_owner TEXT,
        lease_expires_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS fleet_lite_sessions_updated ON fleet_lite_sessions (updated_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_sandbox_resources (
        session_id TEXT NOT NULL REFERENCES fleet_lite_sessions(session_id) ON DELETE CASCADE,
        agent_id TEXT NOT NULL,
        epoch BIGINT NOT NULL,
        allocation_nonce TEXT NOT NULL,
        provider_id TEXT,
        parent_agent_id TEXT,
        action TEXT NOT NULL CHECK (action IN ('create', 'stop', 'delete')),
        intended_action TEXT NOT NULL CHECK (intended_action IN ('stop', 'delete')),
        confirmed BOOLEAN NOT NULL DEFAULT FALSE,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (session_id, agent_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_turns (
        session_id TEXT NOT NULL REFERENCES fleet_lite_sessions(session_id) ON DELETE CASCADE,
        turn_id TEXT NOT NULL,
        seq BIGSERIAL NOT NULL,
        idempotency_key TEXT NOT NULL,
        task_sha256 TEXT NOT NULL,
        task TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'running'
            CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
        result JSONB NOT NULL DEFAULT '{}'::jsonb,
        record JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (session_id, turn_id),
        UNIQUE (session_id, idempotency_key)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS fleet_lite_turns_completed_seq
    ON fleet_lite_turns (session_id, seq DESC)
    WHERE status = 'completed'
    """,
    "CREATE INDEX IF NOT EXISTS fleet_lite_turns_status ON fleet_lite_turns (status, updated_at DESC)",
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
    CREATE INDEX IF NOT EXISTS fleet_lite_memories_content_fts
    ON fleet_lite_memories USING GIN (to_tsvector('english', content))
    """,
    """
    CREATE INDEX IF NOT EXISTS fleet_lite_memories_reviewed
    ON fleet_lite_memories (session_id, reviewed, updated_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_replay_blobs (
        sha256 TEXT PRIMARY KEY,
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        content BYTEA NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_replay_snapshots (
        session_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        manifest JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (session_id, turn_id),
        FOREIGN KEY (session_id, turn_id)
            REFERENCES fleet_lite_turns(session_id, turn_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS fleet_lite_replay_snapshots_turn ON fleet_lite_replay_snapshots (turn_id)",
    "CREATE INDEX IF NOT EXISTS fleet_lite_replay_snapshots_created ON fleet_lite_replay_snapshots (created_at)",
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_review_labels (
        session_id TEXT NOT NULL REFERENCES fleet_lite_sessions(session_id) ON DELETE CASCADE,
        turn_id TEXT NOT NULL,
        task TEXT NOT NULL,
        workspace_manifest TEXT NOT NULL,
        context_files JSONB NOT NULL,
        review_status TEXT NOT NULL CHECK (review_status IN ('approved', 'rejected', 'benchmark', 'synthetic')),
        source_group TEXT NOT NULL DEFAULT '',
        expected_answer TEXT,
        expected_values JSONB NOT NULL DEFAULT '[]'::jsonb,
        structured_expectations JSONB NOT NULL DEFAULT '{}'::jsonb,
        route TEXT CHECK (route IS NULL OR route IN ('Direct', 'RLM')),
        critical BOOLEAN NOT NULL DEFAULT FALSE,
        reviewer TEXT NOT NULL DEFAULT '',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (session_id, turn_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS fleet_lite_review_labels_status
    ON fleet_lite_review_labels (review_status, updated_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_leases (
        scope TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        epoch BIGINT NOT NULL DEFAULT 1,
        expires_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_global_settings (
        scope TEXT PRIMARY KEY,
        settings JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fleet_lite_optimization_runs (
        run_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL DEFAULT 'optimize'
            CHECK (kind IN ('optimize', 'evaluate_rescore', 'evaluate_replay', 'evaluate_synthetic')),
        options JSONB NOT NULL DEFAULT '{}'::jsonb,
        owner_id TEXT NOT NULL,
        managed BOOLEAN NOT NULL DEFAULT FALSE,
        status TEXT NOT NULL,
        stage TEXT NOT NULL,
        stop_requested BOOLEAN NOT NULL DEFAULT FALSE,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS fleet_lite_one_active_optimization_run
    ON fleet_lite_optimization_runs ((1))
    WHERE status IN ('running', 'approving')
    """,
    """
    CREATE INDEX IF NOT EXISTS fleet_lite_optimization_runs_updated
    ON fleet_lite_optimization_runs (updated_at DESC)
    """,
)

_V1_STATEMENTS = SCHEMA_STATEMENTS
_V2_STATEMENTS = (
    "ALTER TABLE fleet_lite_sessions RENAME TO sessions",
    "ALTER TABLE fleet_lite_sandbox_resources RENAME TO sandbox_resources",
    "ALTER TABLE fleet_lite_turns RENAME TO turns",
    "ALTER TABLE fleet_lite_memories RENAME TO notes",
    "ALTER TABLE fleet_lite_replay_blobs RENAME TO replay_blobs",
    "ALTER TABLE fleet_lite_replay_snapshots RENAME TO replay_snapshots",
    "ALTER TABLE fleet_lite_review_labels RENAME TO review_labels",
    "ALTER TABLE fleet_lite_leases RENAME TO leases",
    "ALTER TABLE fleet_lite_global_settings RENAME TO global_settings",
    "ALTER TABLE fleet_lite_optimization_runs RENAME TO jobs",
    """DO $$ DECLARE r RECORD; n TEXT; BEGIN
        FOR r IN SELECT conname, conrelid FROM pg_constraint
            WHERE connamespace = current_schema()::regnamespace AND conname LIKE 'fleet_lite_%'
        LOOP
            n := replace(replace(regexp_replace(r.conname, '^fleet_lite_', ''),
                'memories', 'notes'), 'optimization_runs', 'jobs');
            EXECUTE format('ALTER TABLE %s RENAME CONSTRAINT %I TO %I', r.conrelid::regclass, r.conname, n);
        END LOOP;
        FOR r IN SELECT relname, relkind FROM pg_class
            WHERE relnamespace = current_schema()::regnamespace AND relname LIKE 'fleet_lite_%'
                AND relkind IN ('i', 'S')
        LOOP
            n := replace(replace(regexp_replace(r.relname, '^fleet_lite_', ''),
                'memories', 'notes'), 'optimization_runs', 'jobs');
            IF n = 'one_active_optimization_run' THEN n := 'jobs_one_active'; END IF;
            EXECUTE format('ALTER %s %I.%I RENAME TO %I',
                CASE WHEN r.relkind = 'i' THEN 'INDEX' ELSE 'SEQUENCE' END,
                current_schema(), r.relname, n);
        END LOOP;
    END $$""",
    "ALTER TABLE sessions ADD CHECK (turn_count >= 0 AND ownership_epoch >= 0)",
    "ALTER TABLE sessions ADD COLUMN workspace_indexed_at TIMESTAMPTZ",
    "ALTER TABLE notes ADD CHECK (jsonb_typeof(sources) = 'object')",
    "ALTER TABLE replay_snapshots ADD CHECK (jsonb_typeof(manifest) = 'object')",
    "ALTER TABLE replay_blobs ADD CHECK (size_bytes = octet_length(content))",
    (
        "ALTER TABLE review_labels ADD FOREIGN KEY (session_id, turn_id) REFERENCES "
        "replay_snapshots(session_id, turn_id) ON DELETE CASCADE"
    ),
    "ALTER TABLE turns ADD COLUMN route TEXT CHECK (route IN ('direct', 'flex', 'rlm'))",
    "ALTER TABLE turns ADD COLUMN regenerates_turn_id TEXT",
    "ALTER TABLE turns ADD FOREIGN KEY (session_id, regenerates_turn_id) REFERENCES turns(session_id, turn_id)",
    """CREATE TABLE messages (
        session_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        position INTEGER NOT NULL CHECK (position IN (0, 1)),
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        content JSONB NOT NULL CHECK (jsonb_typeof(content) = 'array'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (session_id, message_id),
        UNIQUE (session_id, turn_id, position),
        UNIQUE (session_id, turn_id, role),
        FOREIGN KEY (session_id, turn_id) REFERENCES turns(session_id, turn_id) ON DELETE CASCADE
    )""",
    """INSERT INTO messages(session_id, message_id, turn_id, position, role, content)
        SELECT session_id, turn_id || '_user', turn_id, 0, 'user',
            jsonb_build_array(jsonb_build_object('type', 'text', 'text', task)) FROM turns""",
    """INSERT INTO messages(session_id, message_id, turn_id, position, role, content)
        SELECT session_id, turn_id || '_assistant', turn_id, 1, 'assistant',
            jsonb_build_array(jsonb_build_object('type', 'text', 'text',
                COALESCE(result->>'answer', record->>'answer', '')))
        FROM turns WHERE status = 'completed'""",
    """CREATE TABLE files (
        session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
        file_id TEXT NOT NULL,
        turn_id TEXT,
        origin TEXT NOT NULL CHECK (origin IN ('upload', 'artifact')),
        filename TEXT NOT NULL,
        media_type TEXT NOT NULL,
        size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
        sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
        bucket TEXT NOT NULL,
        object_key TEXT NOT NULL UNIQUE,
        workspace_path TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (session_id, file_id),
        FOREIGN KEY (session_id, turn_id) REFERENCES turns(session_id, turn_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE message_files (
        session_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        file_id TEXT NOT NULL,
        PRIMARY KEY (session_id, message_id, file_id),
        FOREIGN KEY (session_id, message_id) REFERENCES messages(session_id, message_id) ON DELETE CASCADE,
        FOREIGN KEY (session_id, file_id) REFERENCES files(session_id, file_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE workspace_files (
        session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        size_bytes BIGINT CHECK (size_bytes >= 0),
        sha256 TEXT,
        observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (session_id, path)
    )""",
)
_SCHEMA_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = ((1, _V1_STATEMENTS), (2, _V2_STATEMENTS))


class IdempotencyConflictError(ValueError):
    """Raised when an idempotency key is reused with different Turn inputs."""


def _optimization_owner_id(value: Any) -> str:
    """Validate an optimization owner identifier."""
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("optimization owner_id must contain 1-200 characters")
    return value


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    """Read a column value from either a dict row or positional sequence row."""
    if isinstance(row, dict):
        return row[key]
    return row[index]


def _retry_once(method: Callable[..., Any]) -> Callable[..., Any]:
    """Retry once after a brief backoff on transient PostgreSQL connection errors."""

    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return method(*args, **kwargs)
        except psycopg.OperationalError:
            time.sleep(_RETRY_DELAY_SECONDS)
            return method(*args, **kwargs)

    return wrapper
