"""Content-addressed replay snapshots, review labels, and bounded retention cleanup."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from psycopg.rows import dict_row

from fleet_rlm.replay import rehydrate_snapshot_from_storage, split_snapshot_for_storage
from fleet_rlm.store.schema import _REPLAY_RETENTION_DAYS, _retry_once, _row_value


def _store_replay_snapshot(
    connection: Any,
    session_id: str,
    turn_id: str,
    digest: str,
    content: bytes,
) -> None:
    if len(content) > 16_000_000 or hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("Replay snapshot digest or size validation failed")
    manifest, blobs = split_snapshot_for_storage(digest, content)
    for blob_sha256, size_bytes, blob_bytes in blobs:
        connection.execute(
            "INSERT INTO fleet_lite_replay_blobs (sha256, size_bytes, content) "
            "VALUES (%s, %s, %s) ON CONFLICT (sha256) DO NOTHING",
            (blob_sha256, size_bytes, blob_bytes),
        )
    connection.execute(
        "INSERT INTO fleet_lite_replay_snapshots (session_id, turn_id, sha256, manifest) "
        "VALUES (%s, %s, %s, %s::jsonb) ON CONFLICT (session_id, turn_id) DO NOTHING",
        (session_id, turn_id, digest, json.dumps(manifest, sort_keys=True, separators=(",", ":"))),
    )
    stored_digest = connection.execute(
        "SELECT sha256 FROM fleet_lite_replay_snapshots WHERE session_id = %s AND turn_id = %s",
        (session_id, turn_id),
    ).fetchone()
    if not stored_digest or _row_value(stored_digest, "sha256", 0) != digest:
        raise ValueError("Turn already has a different immutable replay snapshot")


class ReplayStoreMixin:
    """Replay snapshot storage, content-addressed blob deduplication, and review curation."""

    pool: Any
    _store_replay_snapshot = staticmethod(_store_replay_snapshot)

    @staticmethod
    def _load_blob_map(connection: Any, manifests: list[dict[str, Any]]) -> dict[str, bytes]:
        digests: set[str] = set()
        for manifest in manifests:
            raw_sha = manifest.get("_raw_blob_sha256")
            if isinstance(raw_sha, str):
                digests.add(raw_sha)
            blob_refs = manifest.get("replay_file_blobs")
            if isinstance(blob_refs, dict):
                digests.update(value for value in blob_refs.values() if isinstance(value, str))
        if not digests:
            return {}
        rows = connection.execute(
            "SELECT sha256, content FROM fleet_lite_replay_blobs WHERE sha256 = ANY(%s::text[])",
            (sorted(digests),),
        ).fetchall()
        return {str(_row_value(row, "sha256", 0)): bytes(_row_value(row, "content", 1)) for row in rows}

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
                    session_id, turn_id, task, workspace_manifest, context_files, review_status,
                    source_group, expected_answer, expected_values, route, critical, reviewer,
                    structured_expectations
                )
                SELECT %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb
                WHERE EXISTS (
                    SELECT 1 FROM fleet_lite_replay_snapshots
                    WHERE session_id = %s AND turn_id = %s AND sha256 = %s
                )
                ON CONFLICT (session_id, turn_id) DO UPDATE SET
                    task = EXCLUDED.task,
                    workspace_manifest = EXCLUDED.workspace_manifest,
                    context_files = EXCLUDED.context_files,
                    review_status = EXCLUDED.review_status,
                    source_group = EXCLUDED.source_group,
                    expected_answer = EXCLUDED.expected_answer,
                    expected_values = EXCLUDED.expected_values,
                    route = EXCLUDED.route,
                    critical = EXCLUDED.critical,
                    reviewer = EXCLUDED.reviewer,
                    structured_expectations = EXCLUDED.structured_expectations,
                    updated_at = NOW()
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
    def reviewed_examples(self, limit: int) -> list[dict[str, Any]]:
        """Approved, gold-labeled turns across Sessions with rehydrated immutable replay snapshots."""
        with self.pool.connection() as connection:
            rows = (
                connection.cursor(row_factory=dict_row)
                .execute(
                    """
                    SELECT labels.session_id,
                           labels.turn_id,
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
                           replay.manifest AS replay_manifest
                    FROM fleet_lite_review_labels AS labels
                    JOIN fleet_lite_replay_snapshots AS replay
                      ON replay.session_id = labels.session_id AND replay.turn_id = labels.turn_id
                    WHERE labels.review_status = 'approved'
                      AND (NULLIF(labels.expected_answer, '') IS NOT NULL
                           OR jsonb_array_length(labels.expected_values) > 0)
                    ORDER BY labels.updated_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                .fetchall()
            )
            manifests = []
            for row in rows:
                manifest = row.get("replay_manifest")
                if isinstance(manifest, str):
                    manifest = json.loads(manifest)
                    row["replay_manifest"] = manifest
                if isinstance(manifest, dict):
                    manifests.append(manifest)
            blob_map = self._load_blob_map(connection, manifests)
        examples: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            manifest = item.pop("replay_manifest", None)
            if isinstance(manifest, str):
                manifest = json.loads(manifest)
            if isinstance(manifest, dict) and "replay_snapshot_sha256" in item:
                _digest, compressed = rehydrate_snapshot_from_storage(
                    item["replay_snapshot_sha256"], manifest, blob_map
                )
                item["replay_snapshot"] = compressed
            examples.append(item)
        return examples

    @_retry_once
    def new_review_labels(self, since: str | None) -> int:
        """Count approved labels written after `since` (an ISO timestamp), or all of them when None."""
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM fleet_lite_review_labels "
                "WHERE review_status = 'approved' AND (%s::timestamptz IS NULL OR updated_at > %s::timestamptz)",
                (since, since),
            ).fetchone()
        return int(_row_value(row, "count", 0)) if row else 0

    @_retry_once
    def improve_queue(self, limit: int, offset: int = 0) -> list[dict[str, Any]]:
        """Replay-captured Turns across Sessions, newest first, with their label state when reviewed."""
        with self.pool.connection() as connection:
            return (
                connection.cursor(row_factory=dict_row)
                .execute(
                    """
                    SELECT replay.session_id,
                           replay.turn_id,
                           replay.sha256 AS replay_snapshot_sha256,
                           turns.result->>'answer' AS answer,
                           turns.result->>'trace_id' AS trace_id,
                           turns.result->>'sandbox_errors' AS sandbox_errors,
                           turns.result->>'submit_rejections' AS submit_rejections,
                           turns.updated_at,
                           COALESCE(labels.task, NULLIF(turns.task, ''), turns.record->>'task') AS task,
                           labels.review_status,
                           labels.expected_answer,
                           labels.expected_values,
                           labels.critical,
                           labels.source_group
                    FROM fleet_lite_replay_snapshots AS replay
                    JOIN fleet_lite_turns AS turns
                      ON turns.session_id = replay.session_id AND turns.turn_id = replay.turn_id
                    LEFT JOIN fleet_lite_review_labels AS labels
                      ON labels.session_id = replay.session_id AND labels.turn_id = replay.turn_id
                    WHERE turns.status = 'completed'
                    ORDER BY turns.updated_at DESC, replay.session_id, replay.turn_id
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
                .fetchall()
            )

    @_retry_once
    def turn_reference(self, turn_id: str) -> dict[str, Any] | None:
        """Resolve a replay-captured Turn ID to its Session, trace, and snapshot digest, or None."""
        with self.pool.connection() as connection:
            return (
                connection.cursor(row_factory=dict_row)
                .execute(
                    """
                    SELECT replay.session_id,
                           replay.turn_id,
                           replay.sha256 AS replay_snapshot_sha256,
                           turns.result->>'trace_id' AS trace_id,
                           turns.result->>'answer' AS answer
                    FROM fleet_lite_replay_snapshots AS replay
                    JOIN fleet_lite_turns AS turns
                      ON turns.session_id = replay.session_id AND turns.turn_id = replay.turn_id
                    WHERE replay.turn_id = %s
                    ORDER BY turns.updated_at DESC
                    LIMIT 1
                    """,
                    (turn_id,),
                )
                .fetchone()
            )

    @_retry_once
    def replay_snapshot(self, session_id: str, turn_id: str) -> tuple[str, bytes] | None:
        """Fetch and rehydrate immutable replay evidence by its owning Session and Turn."""
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT sha256, manifest FROM fleet_lite_replay_snapshots WHERE session_id = %s AND turn_id = %s",
                (session_id, turn_id),
            ).fetchone()
            if not row:
                return None
            digest = str(_row_value(row, "sha256", 0))
            manifest = _row_value(row, "manifest", 1)
            if isinstance(manifest, str):
                manifest = json.loads(manifest)
            if not isinstance(manifest, dict):
                raise ValueError("Replay snapshot manifest is corrupt")
            blob_map = self._load_blob_map(connection, [manifest])
        return rehydrate_snapshot_from_storage(digest, manifest, blob_map)

    @_retry_once
    def cleanup_expired_replays(
        self,
        *,
        retention_days: int = _REPLAY_RETENTION_DAYS,
        batch_limit: int = 500,
    ) -> tuple[int, int]:
        """Prune expired uncurated snapshots and unreferenced content-addressed blobs."""
        with self.pool.connection() as connection, connection.transaction():
            deleted_snapshots = connection.execute(
                """
                DELETE FROM fleet_lite_replay_snapshots
                WHERE ctid IN (
                    SELECT s.ctid
                    FROM fleet_lite_replay_snapshots AS s
                    WHERE s.created_at < NOW() - (%s * INTERVAL '1 day')
                      AND NOT EXISTS (
                          SELECT 1 FROM fleet_lite_review_labels AS l
                          WHERE l.session_id = s.session_id AND l.turn_id = s.turn_id
                      )
                    LIMIT %s
                )
                RETURNING session_id
                """,
                (retention_days, batch_limit),
            ).fetchall()
            deleted_blobs = connection.execute(
                """
                DELETE FROM fleet_lite_replay_blobs AS b
                WHERE b.ctid IN (
                    SELECT candidate.ctid
                    FROM fleet_lite_replay_blobs AS candidate
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM fleet_lite_replay_snapshots AS s,
                             jsonb_each_text(COALESCE(s.manifest->'replay_file_blobs', '{}'::jsonb)) AS ref(path, sha)
                        WHERE ref.sha = candidate.sha256
                    )
                      AND NOT EXISTS (
                        SELECT 1 FROM fleet_lite_replay_snapshots AS s
                        WHERE s.manifest->>'_raw_blob_sha256' = candidate.sha256
                    )
                    LIMIT %s
                )
                RETURNING sha256
                """,
                (batch_limit,),
            ).fetchall()
        return len(deleted_snapshots), len(deleted_blobs)
