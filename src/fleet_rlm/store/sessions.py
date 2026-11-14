"""Session state, sandbox resource ownership, memories, settings, and Turn persistence."""

from __future__ import annotations

import json
from contextlib import suppress
from typing import Any

from psycopg.rows import dict_row

from fleet_rlm.store.replays import _store_replay_snapshot
from fleet_rlm.store.schema import _retry_once, _row_value

_MAX_SANDBOX_RESOURCES = 32


class SessionStoreMixin:
    """Session, sandbox resource ownership, settings, memory, and Turn methods for Store."""

    pool: Any

    @_retry_once
    def ensure_session(self, session_id: str) -> bool:
        """Create a Session row if absent and report whether it was newly inserted."""
        with self.pool.connection() as connection:
            created = connection.execute(
                "INSERT INTO fleet_lite_sessions (session_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING session_id",
                (session_id,),
            ).fetchone()
        return created is not None

    @_retry_once
    def advance_sandbox_ownership_epoch(self, session_id: str) -> int | None:
        """Advance the current Session's sandbox ownership epoch."""
        with self.pool.connection() as connection, connection.transaction():
            row = connection.execute(
                "UPDATE fleet_lite_sessions SET ownership_epoch = ownership_epoch + 1, updated_at = NOW() "
                "WHERE session_id = %s RETURNING ownership_epoch",
                (session_id,),
            ).fetchone()
        return int(_row_value(row, "ownership_epoch", 0)) if row else None

    @_retry_once
    def sandbox_ownership(self, session_id: str) -> dict[str, Any] | None:
        """Read normalized sandbox ownership state and bounded resource records."""
        with self.pool.connection() as connection:
            session_row = connection.execute(
                "SELECT ownership_epoch FROM fleet_lite_sessions WHERE session_id = %s",
                (session_id,),
            ).fetchone()
            if session_row is None:
                return None
            epoch = _row_value(session_row, "ownership_epoch", 0)
            if type(epoch) is not int:
                raise RuntimeError("Stored sandbox ownership epoch is invalid")
            rows = (
                connection.cursor(row_factory=dict_row)
                .execute(
                    """
                    SELECT agent_id, allocation_nonce, provider_id, parent_agent_id,
                           action, intended_action, confirmed
                    FROM fleet_lite_sandbox_resources
                    WHERE session_id = %s
                    ORDER BY agent_id
                    """,
                    (session_id,),
                )
                .fetchall()
            )
        if len(rows) > _MAX_SANDBOX_RESOURCES:
            raise RuntimeError("Stored sandbox ownership record exceeds resource limit")
        return {"epoch": epoch, "resources": list(rows)}

    @_retry_once
    def mutate_sandbox_resource(
        self, session_id: str, epoch: int, agent_id: str, resource: dict[str, Any] | None
    ) -> bool:
        """Atomically upsert or delete one bounded sandbox resource under `FOR SHARE` epoch lock."""
        if not agent_id or len(agent_id) > 64:
            raise ValueError("Invalid sandbox agent identity")
        encoded = json.dumps(resource, separators=(",", ":")) if resource is not None else None
        if encoded is not None and len(encoded) > 2_000:
            raise ValueError("Sandbox resource intent exceeds its size limit")
        with self.pool.connection() as connection, connection.transaction():
            row = connection.execute(
                "SELECT ownership_epoch FROM fleet_lite_sessions WHERE session_id = %s FOR SHARE",
                (session_id,),
            ).fetchone()
            current_epoch = _row_value(row, "ownership_epoch", 0) if row is not None else None
            if current_epoch != epoch:
                return False
            if resource is None:
                connection.execute(
                    "DELETE FROM fleet_lite_sandbox_resources WHERE session_id = %s AND agent_id = %s",
                    (session_id, agent_id),
                )
                return True
            existing = connection.execute(
                "SELECT agent_id FROM fleet_lite_sandbox_resources WHERE session_id = %s",
                (session_id,),
            ).fetchall()
            existing_ids = {str(_row_value(item, "agent_id", 0)) for item in existing}
            if agent_id not in existing_ids and len(existing_ids) >= _MAX_SANDBOX_RESOURCES:
                raise RuntimeError("Session sandbox ownership resource limit reached")
            connection.execute(
                """
                INSERT INTO fleet_lite_sandbox_resources (
                    session_id, agent_id, epoch, allocation_nonce, provider_id,
                    parent_agent_id, action, intended_action, confirmed, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (session_id, agent_id) DO UPDATE SET
                    epoch = EXCLUDED.epoch,
                    allocation_nonce = EXCLUDED.allocation_nonce,
                    provider_id = EXCLUDED.provider_id,
                    parent_agent_id = EXCLUDED.parent_agent_id,
                    action = EXCLUDED.action,
                    intended_action = EXCLUDED.intended_action,
                    confirmed = EXCLUDED.confirmed,
                    updated_at = NOW()
                """,
                (
                    session_id,
                    agent_id,
                    epoch,
                    resource["allocation_nonce"],
                    resource.get("provider_id"),
                    resource.get("parent_agent_id"),
                    resource["action"],
                    resource["intended_action"],
                    bool(resource.get("confirmed", False)),
                ),
            )
        return True

    @_retry_once
    def claim_environment_report(self, session_id: str) -> bool:
        """Atomically claim this Session's one-time environment observation."""
        with self.pool.connection() as connection:
            claimed = connection.execute(
                "UPDATE fleet_lite_sessions SET environment_reported = TRUE "
                "WHERE session_id = %s AND environment_reported = FALSE RETURNING session_id",
                (session_id,),
            ).fetchone()
        return claimed is not None

    @_retry_once
    def list_sessions(self) -> list[dict[str, Any]]:
        """Return the 100 most recently updated Sessions with their completed Turn counts."""
        with self.pool.connection() as connection:
            return (
                connection.cursor(row_factory=dict_row)
                .execute(
                    "SELECT session_id, updated_at, turn_count "
                    "FROM fleet_lite_sessions ORDER BY updated_at DESC LIMIT 100"
                )
                .fetchall()
            )

    @_retry_once
    def history(self, session_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        """Return chronological completed Turn records for a Session (up to `limit`)."""
        with self.pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT record FROM fleet_lite_turns
                WHERE session_id = %s AND status = 'completed'
                ORDER BY seq DESC
                LIMIT %s
                """,
                (session_id, limit),
            ).fetchall()
        records = [_row_value(row, "record", 0) for row in reversed(rows)]
        return [json.loads(item) if isinstance(item, str) else item for item in records]

    @_retry_once
    def runtime_settings(self, session_id: str) -> dict[str, Any]:
        """Return the Session's saved runtime settings, or an empty dict."""
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT settings FROM fleet_lite_sessions WHERE session_id = %s",
                (session_id,),
            ).fetchone()
        if not row:
            return {}
        value = _row_value(row, "settings", 0)
        if isinstance(value, str):
            return json.loads(value)
        return value or {}

    @_retry_once
    def save_runtime_settings(self, session_id: str, settings: dict[str, Any]) -> None:
        """Merge settings into the Session's JSONB settings column."""
        with self.pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO fleet_lite_sessions (session_id, settings)
                VALUES (%s, %s::jsonb)
                ON CONFLICT (session_id) DO UPDATE SET
                    settings = fleet_lite_sessions.settings || EXCLUDED.settings,
                    updated_at = NOW()
                """,
                (session_id, json.dumps(settings)),
            )

    @_retry_once
    def global_settings(self, scope: str) -> dict[str, Any]:
        """Return a global scope's saved settings, or an empty dict."""
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT settings FROM fleet_lite_global_settings WHERE scope = %s",
                (scope,),
            ).fetchone()
        if not row:
            return {}
        value = _row_value(row, "settings", 0)
        return json.loads(value) if isinstance(value, str) else (value or {})

    @_retry_once
    def update_global_settings(self, scope: str, changes: dict[str, Any]) -> None:
        """Merge `changes` into a scope's settings in SQL; a None value removes its key."""
        removed = [name for name, value in changes.items() if value is None]
        kept = {name: value for name, value in changes.items() if value is not None}
        with self.pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO fleet_lite_global_settings (scope, settings)
                VALUES (%s, %s::jsonb)
                ON CONFLICT (scope) DO UPDATE SET
                    settings = (fleet_lite_global_settings.settings - %s::text[]) || EXCLUDED.settings,
                    updated_at = NOW()
                """,
                (scope, json.dumps(kept), removed),
            )

    @_retry_once
    def save_memory(
        self, session_id: str, memory_id: str, content: str, kind: str, sources: dict[str, str], reviewed: bool
    ) -> None:
        """Insert or replace one memory row, keyed by Session and memory ID."""
        with self.pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO fleet_lite_memories (session_id, memory_id, content, kind, sources, reviewed)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (session_id, memory_id) DO UPDATE SET
                    content = EXCLUDED.content,
                    kind = EXCLUDED.kind,
                    sources = EXCLUDED.sources,
                    reviewed = EXCLUDED.reviewed,
                    updated_at = NOW()
                """,
                (session_id, memory_id, content, kind, json.dumps(sources), reviewed),
            )

    @_retry_once
    def memories(self, session_id: str, search: str, *, reviewed_only: bool) -> list[dict[str, Any]]:
        """Memories matching a full-text search, best match first."""
        with self.pool.connection() as connection:
            cursor = connection.cursor(row_factory=dict_row)
            rows = cursor.execute(
                """
                SELECT memory_id, content, kind, sources, reviewed, updated_at
                FROM fleet_lite_memories
                WHERE session_id = %s
                  AND (%s = FALSE OR reviewed = TRUE)
                  AND (%s = '' OR to_tsvector('english', content) @@ websearch_to_tsquery('english', %s))
                ORDER BY ts_rank_cd(to_tsvector('english', content), websearch_to_tsquery('english', %s)) DESC,
                         updated_at DESC
                LIMIT 20
                """,
                (session_id, reviewed_only, search, search, search),
            ).fetchall()
        for row in rows:
            stamp = row.get("updated_at")
            if hasattr(stamp, "isoformat"):
                row["updated_at"] = stamp.isoformat()
        return rows

    @_retry_once
    def delete_memory(self, session_id: str, memory_id: str) -> None:
        """Delete one memory row from a Session."""
        with self.pool.connection() as connection:
            connection.execute(
                "DELETE FROM fleet_lite_memories WHERE session_id = %s AND memory_id = %s",
                (session_id, memory_id),
            )

    @_retry_once
    def claim_turn(
        self,
        session_id: str,
        turn_id: str,
        key: str,
        task_sha256: str,
        *,
        task: str = "",
        ownership_epoch: int | None = None,
    ) -> dict[str, Any] | None:
        """Record Turn intent; None when newly claimed, else the prior attempt with the same key."""
        with self.pool.connection() as connection, connection.transaction():
            if ownership_epoch is not None:
                self._assert_ownership_epoch(connection, session_id, ownership_epoch)
            inserted = connection.execute(
                """
                INSERT INTO fleet_lite_turns (session_id, turn_id, idempotency_key, task_sha256, task)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (session_id, idempotency_key) DO NOTHING
                RETURNING turn_id
                """,
                (session_id, turn_id, key, task_sha256, task),
            ).fetchone()
            if inserted:
                return None
            prior = (
                connection.cursor(row_factory=dict_row)
                .execute(
                    "SELECT turn_id, task_sha256, status, result FROM fleet_lite_turns "
                    "WHERE session_id = %s AND idempotency_key = %s",
                    (session_id, key),
                )
                .fetchone()
            )
        if prior is None:
            raise RuntimeError("Turn intent disappeared during claim")
        return prior

    @_retry_once
    def turn_by_key(self, session_id: str, key: str) -> dict[str, Any] | None:
        """Read an existing Turn receipt without claiming a new Turn."""
        with self.pool.connection() as connection:
            return (
                connection.cursor(row_factory=dict_row)
                .execute(
                    "SELECT turn_id, task_sha256, status, result FROM fleet_lite_turns "
                    "WHERE session_id = %s AND idempotency_key = %s",
                    (session_id, key),
                )
                .fetchone()
            )

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
                        "UPDATE fleet_lite_turns SET status = %s, result = %s::jsonb, updated_at = NOW() "
                        "WHERE session_id = %s AND turn_id = %s AND status <> 'completed'",
                        (status, json.dumps(result), session_id, turn_id),
                    )
                return
            connection.execute(
                "UPDATE fleet_lite_turns SET status = %s, result = %s::jsonb, updated_at = NOW() "
                "WHERE session_id = %s AND turn_id = %s AND status <> 'completed'",
                (status, json.dumps(result), session_id, turn_id),
            )

    @staticmethod
    def _assert_ownership_epoch(connection: Any, session_id: str, ownership_epoch: int) -> None:
        row = connection.execute(
            "SELECT ownership_epoch FROM fleet_lite_sessions WHERE session_id = %s FOR UPDATE",
            (session_id,),
        ).fetchone()
        raw_epoch = _row_value(row, "ownership_epoch", 0) if row is not None else None
        current = int(raw_epoch) if raw_epoch is not None else None
        if current != ownership_epoch:
            raise RuntimeError("Session ownership epoch changed; stale Turn write was fenced")

    @_retry_once
    def fail_interrupted_turns(self, session_id: str, ownership_epoch: int) -> list[str]:
        """Terminalize prior running Turns after a new lease owner reconciles sandbox resources."""
        result = {
            "event": "error",
            "outcome": "failed",
            "error": "Turn interrupted by process restart; generated code was not replayed.",
        }
        with self.pool.connection() as connection, connection.transaction():
            self._assert_ownership_epoch(connection, session_id, ownership_epoch)
            rows = connection.execute(
                "UPDATE fleet_lite_turns SET status = 'failed', result = %s::jsonb, updated_at = NOW() "
                "WHERE session_id = %s AND status = 'running' RETURNING turn_id",
                (json.dumps(result), session_id),
            ).fetchall()
        return [str(_row_value(row, "turn_id", 0)) for row in rows]

    @_retry_once
    def turn_status(self, session_id: str, turn_id: str) -> dict[str, Any] | None:
        """Return one Turn's status, result, and update time, or None if absent."""
        with self.pool.connection() as connection:
            row = (
                connection.cursor(row_factory=dict_row)
                .execute(
                    "SELECT turn_id, status, result, updated_at FROM fleet_lite_turns "
                    "WHERE session_id = %s AND turn_id = %s",
                    (session_id, turn_id),
                )
                .fetchone()
            )
        if row and hasattr(row.get("updated_at"), "isoformat"):
            row["updated_at"] = row["updated_at"].isoformat()
        return row

    @_retry_once
    def load_session(self, session_id: str, *, limit: int = 8) -> tuple[str, list[dict[str, Any]]]:
        """Return the Session's memory summary and the latest `limit` completed Turn records chronologically."""
        with self.pool.connection() as connection:
            session_row = (
                connection.cursor(row_factory=dict_row)
                .execute(
                    "SELECT memory_summary FROM fleet_lite_sessions WHERE session_id = %s",
                    (session_id,),
                )
                .fetchone()
            )
            turn_rows = connection.execute(
                """
                SELECT record FROM fleet_lite_turns
                WHERE session_id = %s AND status = 'completed'
                ORDER BY seq DESC
                LIMIT %s
                """,
                (session_id, limit),
            ).fetchall()
        summary = str((session_row or {}).get("memory_summary") or "")
        records = [_row_value(row, "record", 0) for row in reversed(turn_rows)]
        parsed = [json.loads(item) if isinstance(item, str) else item for item in records]
        return summary, parsed

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
        """In one transaction: record Turn completion, memory note, optional deduplicated replay, and turn count."""
        del max_turns
        if replay_snapshot is not None and completion is None:
            raise ValueError("A replay snapshot can only be committed with a completed Turn")
        turn_record: dict[str, Any] = {}
        with suppress(TypeError, AttributeError, json.JSONDecodeError):
            loaded = json.loads(turn_record_json)
            if isinstance(loaded, dict):
                turn_record = loaded
        turn_id = turn_record.get("turn_id")
        if not turn_id:
            raise ValueError("Turn record must carry a turn_id")
        task = str(turn_record.get("task") or "")
        note = memory_note[:10_000]
        with self.pool.connection() as connection, connection.transaction():
            connection.execute(
                "INSERT INTO fleet_lite_sessions (session_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (session_id,),
            )
            connection.execute(
                "SELECT session_id FROM fleet_lite_sessions WHERE session_id = %s FOR UPDATE",
                (session_id,),
            )
            if ownership_epoch is not None:
                self._assert_ownership_epoch(connection, session_id, ownership_epoch)
            result_json = json.dumps(completion[1]) if completion is not None else "{}"
            status = "completed"
            previous = connection.execute(
                "SELECT status FROM fleet_lite_turns WHERE session_id = %s AND turn_id = %s",
                (session_id, turn_id),
            ).fetchone()
            was_completed = previous is not None and _row_value(previous, "status", 0) == "completed"
            updated = connection.execute(
                """
                UPDATE fleet_lite_turns
                SET task = CASE WHEN %s <> '' THEN %s ELSE task END,
                    status = %s,
                    result = %s::jsonb,
                    record = %s::jsonb,
                    updated_at = NOW()
                WHERE session_id = %s AND turn_id = %s
                RETURNING turn_id
                """,
                (task, task, status, result_json, turn_record_json, session_id, turn_id),
            ).fetchone()
            if updated is None:
                connection.execute(
                    """
                    INSERT INTO fleet_lite_turns
                        (session_id, turn_id, idempotency_key, task_sha256, task, status, result, record)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    """,
                    (session_id, turn_id, f"commit:{turn_id}", "", task, status, result_json, turn_record_json),
                )
            connection.execute(
                """
                UPDATE fleet_lite_sessions
                SET memory_summary = CASE
                        WHEN %s = '' THEN memory_summary
                        WHEN memory_summary = '' THEN %s
                        ELSE right(memory_summary || E'\n' || %s, 10000)
                    END,
                    turn_count = turn_count + CASE WHEN %s THEN 0 ELSE 1 END,
                    updated_at = NOW()
                WHERE session_id = %s
                """,
                (note, note, note, was_completed, session_id),
            )
            if replay_snapshot is not None:
                assert completion is not None
                _store_replay_snapshot(connection, session_id, completion[0], *replay_snapshot)
