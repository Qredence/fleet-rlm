"""Durable chat messages, published files, and passive workspace inventory."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from psycopg.rows import dict_row


class ChatStoreMixin:
    """Session-scoped browser records; observer reads never acquire runtime leases."""

    pool: Any
    _assert_ownership_epoch: Callable[..., None]

    def messages(self, session_id: str, *, before: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Read a bounded page of complete turns, newest page returned chronologically."""
        with self.pool.connection() as connection:
            rows = (
                connection.cursor(row_factory=dict_row)
                .execute(
                    """SELECT m.*, t.seq, t.status, t.route, t.result->>'trace_id' AS trace_id,
                    t.regenerates_turn_id,
                    COALESCE((SELECT jsonb_agg(to_jsonb(f) - 'bucket' - 'object_key' ORDER BY f.created_at)
                      FROM message_files mf JOIN files f USING(session_id, file_id)
                      WHERE mf.session_id=m.session_id AND mf.message_id=m.message_id), '[]'::jsonb) AS files
                    FROM messages m JOIN turns t USING(session_id, turn_id)
                    WHERE m.session_id=%s AND t.turn_id IN (
                      SELECT turn_id FROM turns WHERE session_id=%s AND (%s::bigint IS NULL OR seq < %s)
                      ORDER BY seq DESC LIMIT %s)
                    ORDER BY t.seq, m.position""",
                    (session_id, session_id, before, before, min(max(limit, 1), 100)),
                )
                .fetchall()
            )
        return list(rows)

    def save_message_activity(self, session_id: str, message_id: str, activity: list[dict[str, Any]]) -> bool:
        """Replace the activity parts on one completed assistant message."""
        encoded = json.dumps(activity, ensure_ascii=False, separators=(",", ":"))
        with self.pool.connection() as connection, connection.transaction():
            row = connection.execute(
                """UPDATE messages AS m
                SET content = %s::jsonb || COALESCE((
                    SELECT jsonb_agg(item.value ORDER BY item.ordinality)
                    FROM jsonb_array_elements(m.content) WITH ORDINALITY AS item(value, ordinality)
                    WHERE item.value->>'type' <> 'data-fleetActivity'
                ), '[]'::jsonb)
                FROM turns AS t
                WHERE m.session_id=%s AND m.message_id=%s AND m.role='assistant'
                  AND t.session_id=m.session_id AND t.turn_id=m.turn_id AND t.status='completed'
                RETURNING m.message_id""",
                (encoded, session_id, message_id),
            ).fetchone()
        return row is not None

    @staticmethod
    def _save_file(connection: Any, session_id: str, file: dict[str, Any]) -> None:
        connection.execute(
            """INSERT INTO files(session_id,file_id,turn_id,origin,filename,media_type,size_bytes,
               sha256,bucket,object_key,workspace_path)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (session_id,file_id) DO NOTHING""",
            (
                session_id,
                *(
                    file[k]
                    for k in (
                        "file_id",
                        "turn_id",
                        "origin",
                        "filename",
                        "media_type",
                        "size_bytes",
                        "sha256",
                        "bucket",
                        "object_key",
                        "workspace_path",
                    )
                ),
            ),
        )

    def save_upload(self, session_id: str, file: dict[str, Any]) -> None:
        """Save a verified object as a session file without acquiring an interpreter."""
        with self.pool.connection() as connection, connection.transaction():
            connection.execute("INSERT INTO sessions(session_id) VALUES (%s) ON CONFLICT DO NOTHING", (session_id,))
            self._save_file(connection, session_id, file)

    def published_files(self, session_id: str) -> list[dict[str, Any]]:
        """Return public metadata only; credentials and storage locators remain on the host."""
        with self.pool.connection() as connection:
            return list(
                connection.cursor(row_factory=dict_row)
                .execute(
                    "SELECT file_id,turn_id,origin,filename,media_type,size_bytes,sha256,workspace_path,created_at "
                    "FROM files WHERE session_id=%s ORDER BY created_at DESC LIMIT 500",
                    (session_id,),
                )
                .fetchall()
            )

    def published_file(self, session_id: str, file_id: str) -> dict[str, Any] | None:
        """Resolve a private storage locator only within its owning session."""
        with self.pool.connection() as connection:
            return (
                connection.cursor(row_factory=dict_row)
                .execute("SELECT * FROM files WHERE session_id=%s AND file_id=%s", (session_id, file_id))
                .fetchone()
            )

    def workspace_inventory(self, session_id: str) -> dict[str, Any]:
        """Read cached workspace paths and the time of the last complete scan."""
        with self.pool.connection() as connection:
            stamp = connection.execute(
                "SELECT workspace_indexed_at FROM sessions WHERE session_id=%s", (session_id,)
            ).fetchone()
            rows = (
                connection.cursor(row_factory=dict_row)
                .execute(
                    "SELECT path,size_bytes,sha256,observed_at FROM workspace_files WHERE session_id=%s ORDER BY path",
                    (session_id,),
                )
                .fetchall()
            )
        return {"indexed_at": stamp[0] if stamp else None, "files": list(rows)}

    def save_workspace_inventory(self, session_id: str, paths: list[str], epoch: int) -> None:
        """Replace only a complete validated inventory while fenced by runtime ownership."""
        from fleet_rlm.workspace import _workspace_relative_path

        paths = sorted({_workspace_relative_path(path) for path in paths})
        with self.pool.connection() as connection, connection.transaction():
            self._assert_ownership_epoch(connection, session_id, epoch)
            connection.execute("DELETE FROM workspace_files WHERE session_id=%s", (session_id,))
            for path in paths:
                connection.execute("INSERT INTO workspace_files(session_id,path) VALUES (%s,%s)", (session_id, path))
            connection.execute("UPDATE sessions SET workspace_indexed_at=NOW() WHERE session_id=%s", (session_id,))

    def bind_turn_submission(
        self, connection: Any, session_id: str, turn_id: str, task: str, submission: dict[str, Any]
    ) -> None:
        """Bind the admitted intent, user message, and session-owned files in its transaction."""
        message_id = submission.get("message_id") or f"{turn_id}_user"
        connection.execute(
            "INSERT INTO messages(session_id,message_id,turn_id,position,role,content) "
            "VALUES (%s,%s,%s,0,'user',%s::jsonb)",
            (session_id, message_id, turn_id, json.dumps([{"type": "text", "text": task}])),
        )
        for file_id in submission.get("file_ids", []):
            row = connection.execute(
                "SELECT file_id FROM files WHERE session_id=%s AND file_id=%s AND origin='upload'",
                (session_id, file_id),
            ).fetchone()
            if row is None:
                raise ValueError("Attachment does not belong to this session")
            connection.execute(
                "INSERT INTO message_files(session_id,message_id,file_id) VALUES (%s,%s,%s)",
                (session_id, message_id, file_id),
            )
        if submission.get("regenerates_turn_id"):
            connection.execute(
                "UPDATE turns SET regenerates_turn_id=%s WHERE session_id=%s AND turn_id=%s",
                (submission["regenerates_turn_id"], session_id, turn_id),
            )
