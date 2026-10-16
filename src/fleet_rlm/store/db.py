"""The Store: every SQL statement Fleet runs, over one psycopg connection pool, plus the harness that mirrors a Session.

Methods take the Session ID explicitly and do no validation beyond SQL; callers validate paths,
sizes and digests. Pooled connections are autocommit, so a single statement costs one network
round trip (about 116 ms to Lakebase from a laptop); only `mirror_files` opens a transaction.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ParamSpec, TypeVar

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.workspace import validate_session_id


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
    CREATE TABLE IF NOT EXISTS fleet_lite_runtime_settings (
        session_id TEXT PRIMARY KEY REFERENCES fleet_lite_sessions(session_id) ON DELETE CASCADE,
        settings JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
)

_UPSERT_FILE = """
    INSERT INTO fleet_lite_vfs_files (session_id, rel_path, sha256, content, updated_at)
    VALUES (%s, %s, %s, %s, NOW())
    ON CONFLICT (session_id, rel_path) DO UPDATE
    SET sha256 = EXCLUDED.sha256, content = EXCLUDED.content, updated_at = NOW()
    WHERE fleet_lite_vfs_files.sha256 IS DISTINCT FROM EXCLUDED.sha256
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
"""


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

    def __init__(self, pool: ConnectionPool, dsn: str) -> None:
        self.pool = pool
        self.dsn = dsn

    @classmethod
    def open(cls, dsn: str, *, wait: bool = False, max_size: int = 10) -> Store:
        """Open a pool. With wait=False the (slow, cold) first connect happens in the background."""
        pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=max_size,
            open=False,
            kwargs={"connect_timeout": 30, "autocommit": True},
            name="fleet",
        )
        pool.open(wait=wait, timeout=60)
        return cls(pool, dsn)

    def close(self) -> None:
        self.pool.close()

    # Schema and Sessions

    @_retry_once
    def initialize(self) -> None:
        with self.pool.connection() as connection:
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)

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
        connection = psycopg.connect(self.dsn, autocommit=True, connect_timeout=30)
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
        """Matching memories, each marked stale when a source file's digest has changed."""
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
            hashes = dict(
                connection.execute(
                    "SELECT rel_path,sha256 FROM fleet_lite_vfs_files WHERE session_id=%s", (session_id,)
                ).fetchall()
            )
        for row in rows:
            row["stale"] = any(hashes.get(path) != digest for path, digest in row["sources"].items())
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

    # Workspace mirror

    @_retry_once
    def load_session(self, session_id: str) -> tuple[list[dict[str, Any]], str, list[Any]]:
        """Create the Session if needed; return its files (rel_path, sha256, content), summary and turns."""
        with self.pool.connection() as connection:
            connection.execute(
                "INSERT INTO fleet_lite_sessions (session_id) VALUES (%s) ON CONFLICT DO NOTHING", (session_id,)
            )
            cursor = connection.cursor(row_factory=dict_row)
            files = cursor.execute(
                "SELECT rel_path, sha256, content FROM fleet_lite_vfs_files WHERE session_id = %s ORDER BY rel_path",
                (session_id,),
            ).fetchall()
            session = cursor.execute(
                "SELECT memory_summary, turns FROM fleet_lite_sessions WHERE session_id = %s", (session_id,)
            ).fetchone()
        return files, str((session or {}).get("memory_summary") or ""), (session or {}).get("turns") or []

    @_retry_once
    def vfs_files(self, session_id: str, rel_paths: list[str]) -> dict[str, dict[str, Any]]:
        with self.pool.connection() as connection:
            rows = (
                connection.cursor(row_factory=dict_row)
                .execute(
                    "SELECT rel_path, sha256, content FROM fleet_lite_vfs_files "
                    "WHERE session_id = %s AND rel_path = ANY(%s)",
                    (session_id, rel_paths),
                )
                .fetchall()
            )
        return {row["rel_path"]: row for row in rows}

    @_retry_once
    def mirror_files(
        self,
        session_id: str,
        files: list[tuple[str, str, bytes]],
        *,
        turn_record_json: str | None = None,
        max_turns: int = 200,
        memory_note: str = "",
        completion: tuple[str, dict[str, Any]] | None = None,
    ) -> None:
        """In one transaction: upsert changed files, drop missing ones, and optionally append a Turn.

        `files` is (rel_path, sha256, content). `completion` is (turn_id, result) for the turn record.
        """
        with self.pool.connection() as connection, connection.transaction():
            connection.execute(
                "INSERT INTO fleet_lite_sessions (session_id) VALUES (%s) ON CONFLICT DO NOTHING", (session_id,)
            )
            connection.execute(
                "SELECT session_id FROM fleet_lite_sessions WHERE session_id = %s FOR UPDATE", (session_id,)
            )
            for rel_path, sha256, content in files:
                connection.execute(_UPSERT_FILE, (session_id, rel_path, sha256, content))
            connection.execute(
                "DELETE FROM fleet_lite_vfs_files WHERE session_id = %s AND NOT (rel_path = ANY(%s))",
                (session_id, [rel_path for rel_path, _, _ in files]),
            )
            if turn_record_json is None:
                connection.execute(
                    "UPDATE fleet_lite_sessions SET updated_at = NOW() WHERE session_id = %s", (session_id,)
                )
            else:
                note = memory_note[:10_000]
                connection.execute(_APPEND_TURN, (turn_record_json, max_turns, note, note, session_id))
            if completion is not None:
                turn_id, result = completion
                connection.execute(
                    "UPDATE fleet_lite_turns SET status='completed',result=%s::jsonb,updated_at=NOW() "
                    "WHERE session_id=%s AND turn_id=%s",
                    (json.dumps(result), session_id, turn_id),
                )

    # Optimization examples

    @_retry_once
    def reviewed_examples(self, session_id: str, default_catalog: str, limit: int) -> list[dict[str, Any]]:
        """Approved, gold-labeled turns with source lineage."""
        with self.pool.connection() as connection:
            return (
                connection.cursor(row_factory=dict_row)
                .execute(
                    """
                    SELECT s.memory_summary,
                           turn->>'task' AS task,
                           COALESCE(turn->>'workspace_manifest', '/workspace/files (empty)') AS workspace_manifest,
                           COALESCE(turn->>'skills_catalog', %s) AS skills_catalog,
                           turn->>'expected_answer' AS answer,
                           turn->>'source_group' AS source_group,
                           turn->'context_files' AS context_files
                    FROM fleet_lite_sessions AS s
                    CROSS JOIN LATERAL jsonb_array_elements(s.turns) AS turn
                    WHERE s.session_id = %s
                      AND turn->>'task' IS NOT NULL
                      AND turn->>'expected_answer' IS NOT NULL
                      AND turn->>'review_status' = 'approved'
                      AND turn->>'source_group' IS NOT NULL
                    ORDER BY s.updated_at DESC
                    LIMIT %s
                    """,
                    (default_catalog, session_id, limit),
                )
                .fetchall()
            )


_MAX_VFS_FILE_BYTES = 25_000_000
_MAX_VFS_SYNC_BYTES = 100_000_000
_MAX_VFS_FILES = 5_000
_MAX_TURN_RECORD_BYTES = 1_000_000
_SESSION_HISTORY_TURNS = 8
_SESSION_HISTORY_CHARS = 16_000
_MAX_STORED_TURNS = 200


@dataclass(slots=True)
class VolumePostgresHarness:
    """Mirror per-Session workspace files and committed Turns to Postgres (SQL lives in the Store)."""

    store: Store
    sandbox_mgr: DaytonaSandboxManager
    _hydrated: bool = field(default=False, init=False)

    @property
    def session_id(self) -> str:
        return self.sandbox_mgr.session_id

    def acquire_session_lease(self) -> psycopg.Connection:
        """Keep a PostgreSQL session lock until runtime close or process death."""
        return self.store.acquire_session_lease(self.session_id)

    def runtime_settings(self) -> dict[str, Any]:
        return self.store.runtime_settings(self.session_id)

    def save_runtime_settings(self, settings: dict[str, Any]) -> None:
        self.store.save_runtime_settings(self.session_id, settings)

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

    def finish_turn(self, turn_id: str, status: str, result: dict[str, Any]) -> None:
        self.store.finish_turn(self.session_id, turn_id, status, result)

    def turn_status(self, turn_id: str) -> dict[str, Any] | None:
        return self.store.turn_status(self.session_id, validate_session_id(turn_id))

    def hydrate_volume_from_postgres(self, session_id: str) -> tuple[str, str]:
        """Restore only the requested Session's files, summary, and bounded history."""
        session_id = validate_session_id(session_id)
        files, summary, turns = self.store.load_session(session_id)
        restored_bytes = 0
        for row in [] if self._hydrated else files:
            content = bytes(row["content"])
            restored_bytes += len(content)
            if (
                not self._valid_vfs_path(row["rel_path"])
                or len(content) > _MAX_VFS_FILE_BYTES
                or restored_bytes > _MAX_VFS_SYNC_BYTES
                or hashlib.sha256(content).hexdigest() != row["sha256"]
            ):
                raise ValueError("Persisted workspace file hash validation failed")
            self.sandbox_mgr.write_workspace_file(row["rel_path"], content)
        self._hydrated = True
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

    def sync_volume_to_postgres(
        self,
        session_id: str,
        turn_record: dict[str, Any],
        new_memory_note: str = "",
    ) -> list[str]:
        """Hash and upsert changed Session files in the same transaction as its Turn."""
        session_id = validate_session_id(session_id)
        files = self._scan_volume_files()
        record_json = json.dumps(turn_record, ensure_ascii=False, separators=(",", ":"))
        if len(record_json.encode("utf-8")) > _MAX_TURN_RECORD_BYTES:
            raise ValueError("Turn record exceeds the persistence size limit")
        verified = self._verified_files(files)
        artifacts = sorted({rel_path for rel_path, _, _ in verified if rel_path.startswith("artifacts/")})
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
        self.store.mirror_files(
            session_id,
            verified,
            turn_record_json=record_json,
            max_turns=_MAX_STORED_TURNS,
            memory_note=new_memory_note,
            completion=completion,
        )
        return artifacts

    def persist_workspace_files(self, session_id: str) -> list[str]:
        """Mirror a Session's current files without recording a synthetic Turn."""
        session_id = validate_session_id(session_id)
        files = self._verified_files(self._scan_volume_files())
        self.store.mirror_files(session_id, files)
        return [rel_path for rel_path, _, _ in files]

    def _verified_files(self, files: list[dict[str, Any]]) -> list[tuple[str, str, bytes]]:
        """Decode a workspace scan into (rel_path, sha256, content), re-checking every path, size and digest."""
        verified = []
        for item in files:
            rel_path = str(item.get("rel", ""))
            if not self._valid_vfs_path(rel_path):
                raise ValueError("Workspace scan returned an invalid relative path")
            content = base64.b64decode(item["b64"], validate=True)
            if len(content) > _MAX_VFS_FILE_BYTES or hashlib.sha256(content).hexdigest() != item.get("sha"):
                raise ValueError("Workspace file hash or size validation failed")
            verified.append((rel_path, str(item["sha"]), content))
        return verified

    def _scan_volume_files(self) -> list[dict[str, Any]]:
        """Read a bounded, symlink-free manifest from the owning Session Volume."""
        paths = self.sandbox_mgr.list_workspace_files()
        if len(paths) > _MAX_VFS_FILES:
            raise ValueError("Workspace contains too many files")
        files = []
        total = 0
        for path in paths:
            if not self._valid_vfs_path(path):
                raise ValueError("Invalid workspace scan path")
            raw = self.sandbox_mgr.read_workspace_file(path, max_bytes=_MAX_VFS_FILE_BYTES)
            total += len(raw)
            if total > _MAX_VFS_SYNC_BYTES:
                raise ValueError("Workspace exceeds sync quota")
            files.append({"rel": path, "sha": hashlib.sha256(raw).hexdigest(), "b64": base64.b64encode(raw).decode()})
        return files

    @staticmethod
    def _valid_vfs_path(rel_path: str) -> bool:
        parts = rel_path.split("/")
        return (
            bool(parts)
            and parts[0] in {"files", "memory", "skills", "artifacts", "buffers", "sessions"}
            and len(rel_path) <= 1024
            and all(part not in {"", ".", ".."} for part in parts)
            and "\\" not in rel_path
            and "\x00" not in rel_path
            and not any(ord(character) < 32 for character in rel_path)
        )
