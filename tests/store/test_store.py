"""Store migrations, history, optimization records, and explicitly opted-in database contracts."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.errors import InsufficientPrivilege

from fleet_rlm.session import SessionRuntime
from fleet_rlm.store import (
    _OPTIMIZATION_LOCK_SCOPE,
    _SCHEMA_MIGRATIONS,
    SCHEMA_STATEMENTS,
    SCHEMA_VERSION,
    IdempotencyConflictError,
    Store,
    lakebase_connection_class,
)

# Store persistence and migrations


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeCursor:
        rendered = sql.as_string() if hasattr(sql, "as_string") else sql
        self.connection.statements.append((" ".join(rendered.split()), params))
        if self.connection.error is not None:
            error, self.connection.error = self.connection.error, None
            raise error
        self.rows = self.connection.replies.pop(0) if self.connection.replies else []
        return self

    def fetchall(self) -> list[Any]:
        return list(self.rows)

    def fetchone(self) -> Any:
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self, replies: list[list[Any]], error: Exception | None = None) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.replies = replies
        self.error = error
        self.closed = False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeCursor:
        return FakeCursor(self).execute(sql, params)

    def cursor(self, **_kwargs: Any) -> FakeCursor:
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True

    @contextmanager
    def transaction(self):
        self.statements.append(("BEGIN", ()))
        yield
        self.statements.append(("COMMIT", ()))


class FakePool:
    def __init__(self, *replies: list[Any], error: Exception | None = None) -> None:
        self.conn = FakeConnection(list(replies), error)
        self.connection_value = self.conn
        self.borrowed = 0

    @contextmanager
    def connection(self):
        self.borrowed += 1
        yield self.conn


def make_store(*replies: list[Any], error: Exception | None = None) -> tuple[Store, FakePool]:
    pool = FakePool(*replies, error=error)
    return Store(pool, "postgresql://fake"), pool  # ty: ignore[invalid-argument-type]


def test_schema_keeps_the_lite_tables_separate_from_the_legacy_schema():
    joined = "\n".join(SCHEMA_STATEMENTS)
    assert SCHEMA_VERSION == 1
    assert len(_SCHEMA_MIGRATIONS) == 1
    assert "CREATE TABLE IF NOT EXISTS fleet_lite_sessions" in joined
    assert "CREATE TABLE IF NOT EXISTS fleet_lite_sandbox_resources" in joined
    assert "CREATE TABLE IF NOT EXISTS fleet_lite_turns" in joined
    assert "CREATE TABLE IF NOT EXISTS fleet_lite_memories" in joined
    assert "CREATE TABLE IF NOT EXISTS fleet_lite_replay_blobs" in joined
    assert "CREATE TABLE IF NOT EXISTS fleet_lite_replay_snapshots" in joined
    assert "CREATE TABLE IF NOT EXISTS fleet_lite_review_labels" in joined
    assert "CREATE TABLE IF NOT EXISTS fleet_lite_leases" in joined
    assert "CREATE TABLE IF NOT EXISTS fleet_lite_global_settings" in joined
    assert "CREATE TABLE IF NOT EXISTS fleet_lite_optimization_runs" in joined
    assert "fleet_lite_vfs_files" not in joined
    assert "fleet_lite_managed_" not in joined
    assert "CREATE TABLE IF NOT EXISTS fleet_sessions" not in joined
    replay_schema = next(statement for statement in SCHEMA_STATEMENTS if "fleet_lite_replay_snapshots" in statement)
    assert "REFERENCES fleet_lite_turns(session_id, turn_id) ON DELETE CASCADE" in replay_schema
    assert "fleet_lite_memories_content_fts" in joined
    assert "fleet_lite_one_active_optimization_run" in joined


def test_reviewed_examples_span_every_session():
    store, pool = make_store([{"turn_id": "t1", "session_id": "s1"}])
    store.reviewed_examples(10)
    sql, params = pool.conn.statements[0]
    assert "labels.session_id" in sql and "WHERE labels.session_id" not in sql
    assert "fleet_lite_replay_snapshots AS replay" in sql
    assert params == (10,)


def test_approved_program_reports_the_latest_approved_digest_and_approvals_in_flight():
    store, pool = make_store([("approving", "d-new"), ("approved", "d-latest"), ("approved", "d-old")])
    assert store.approved_program() == {"approved": "d-latest", "approving": ["d-new"]}
    sql = pool.conn.statements[0][0]
    assert "NOT (metadata ? 'legacy_session_id')" in sql and "kind = 'optimize'" in sql
    store, _pool = make_store([("approving", "d-new")])
    assert store.approved_program() == {"approved": None, "approving": ["d-new"]}
    store, _pool = make_store([])
    assert store.approved_program() == {"approved": None, "approving": []}


def test_new_review_labels_counts_only_approved_labels_after_the_cutoff():
    store, pool = make_store([(3,)])
    assert store.new_review_labels("2026-11-01T00:00:00+00:00") == 3
    assert "review_status = 'approved'" in pool.conn.statements[0][0]
    assert pool.conn.statements[0][1] == ("2026-11-01T00:00:00+00:00",) * 2


def test_claim_environment_report_is_one_atomic_update_per_session():
    store, pool = make_store([("s1",)])

    assert store.claim_environment_report("s1") is True

    statement, parameters = pool.conn.statements[0]
    assert statement == (
        "UPDATE fleet_lite_sessions SET environment_reported = TRUE "
        "WHERE session_id = %s AND environment_reported = FALSE RETURNING session_id"
    )
    assert parameters == ("s1",)

    store, pool = make_store([])
    assert store.claim_environment_report("s1") is False
    assert "RETURNING session_id" in pool.conn.statements[0][0]


def test_sandbox_resource_mutation_locks_session_and_rejects_stale_owner_epoch():
    store, pool = make_store([{"ownership_epoch": 9}])

    assert store.mutate_sandbox_resource("s1", 8, "agent-a", {"action": "create", "intended_action": "stop"}) is False

    sql = [statement for statement, _parameters in pool.conn.statements]
    assert "SELECT ownership_epoch FROM fleet_lite_sessions WHERE session_id = %s FOR SHARE" in sql
    assert not any("fleet_lite_sandbox_resources" in statement for statement in sql)
    assert "COMMIT" in sql


def test_sandbox_resource_mutation_replaces_one_identity_under_the_current_epoch():
    store, pool = make_store([{"ownership_epoch": 9}], [])

    assert (
        store.mutate_sandbox_resource(
            "s1",
            9,
            "agent-a",
            {
                "allocation_nonce": "agent-a",
                "provider_id": "sandbox-a",
                "action": "stop",
                "intended_action": "stop",
            },
        )
        is True
    )

    upsert, parameters = pool.conn.statements[-2]
    assert upsert.startswith("INSERT INTO fleet_lite_sandbox_resources")
    assert "ON CONFLICT (session_id, agent_id) DO UPDATE SET" in upsert
    assert parameters == ("s1", "agent-a", 9, "agent-a", "sandbox-a", None, "stop", "stop", False)

    store_del, pool_del = make_store([{"ownership_epoch": 9}])
    assert store_del.mutate_sandbox_resource("s1", 9, "agent-a", None) is True
    assert "DELETE FROM fleet_lite_sandbox_resources WHERE session_id = %s AND agent_id = %s" in {
        sql for sql, _ in pool_del.conn.statements
    }


def test_review_labels_require_the_selected_immutable_replay_before_writing():
    store, pool = make_store()
    store.replay_snapshot = lambda *_args: None

    with pytest.raises(ValueError, match="snapshot is missing or has changed"):
        store.save_review_labels(
            "s1",
            "t1",
            "a" * 64,
            review_status="approved",
            source_group="group-1",
            expected_answer="safe",
            expected_values=[],
            route=None,
            critical=False,
            reviewer="reviewer-1",
        )

    assert pool.borrowed == 0


def test_review_store_derives_provenance_from_snapshot_not_trace_previews():
    from fleet_rlm.replay import encode_snapshot

    snapshot = {
        "schema_version": 1,
        "session_id": "s1",
        "turn_id": "t1",
        "task": "immutable task",
        "workspace_manifest": "/workspace/files (empty)",
        "context_files": {},
        "replay_files": {},
        "skill_revision_sha256": hashlib.sha256(b"{}").hexdigest(),
    }
    digest, compressed = encode_snapshot(snapshot)
    store, pool = make_store([(1,)])
    store.replay_snapshot = lambda *_args: (digest, compressed)
    store.save_review_labels(
        "s1",
        "t1",
        digest,
        review_status="approved",
        source_group="group",
        expected_answer="4",
        expected_values=["4"],
        route="flex",
        critical=True,
        reviewer="human",
    )
    sql, parameters = pool.conn.statements[0]
    assert "INSERT INTO fleet_lite_review_labels" in sql
    assert parameters[2:5] == ("immutable task", "/workspace/files (empty)", "{}")


def test_every_call_borrows_one_pooled_connection():
    store, pool = make_store([("s1", dt.datetime(2026, 1, 1), 2)], [({"turns": [1]},)])
    store.list_sessions()
    store.runtime_settings("s1")
    assert pool.borrowed == 2


def test_schema_bootstrap_records_an_additive_versioned_baseline():
    store, pool = make_store()
    store.initialize()
    statements = [statement for statement, _ in pool.conn.statements]
    assert statements[0] == "BEGIN"
    lock_index = next(i for i, sql in enumerate(statements) if "pg_advisory_xact_lock" in sql)
    ledger_index = next(
        i for i, sql in enumerate(statements) if "CREATE TABLE IF NOT EXISTS fleet_lite_schema_migrations" in sql
    )
    assert lock_index < ledger_index
    assert any("CREATE TABLE IF NOT EXISTS fleet_lite_schema_migrations" in sql for sql in statements)
    version_index = statements.index("SELECT MAX(version) FROM fleet_lite_schema_migrations")
    assert ledger_index < version_index
    assert "SELECT MAX(version) FROM fleet_lite_schema_migrations" in statements
    assert any("INSERT INTO fleet_lite_schema_migrations(version) VALUES (%s)" in sql for sql in statements)
    assert any("DELETE FROM fleet_lite_replay_snapshots" in sql for sql in statements)
    assert any("DELETE FROM fleet_lite_replay_blobs" in sql for sql in statements)
    assert "COMMIT" in statements


def test_custom_schema_is_created_by_migration_pool_and_runtime_uses_runtime_pool():
    runtime_pool = FakePool([], [], [({"turn_id": "t1"},)])
    migration_pool = FakePool()
    store = Store(
        runtime_pool,  # ty: ignore[invalid-argument-type]
        "postgresql://runtime",
        migration_pool=migration_pool,  # ty: ignore[invalid-argument-type]
        schema="fleet_rlm_lite",
    )

    store.initialize()
    assert store.history("s1") == [{"turn_id": "t1"}]

    migration_sql = [sql for sql, _ in migration_pool.conn.statements]
    assert 'CREATE SCHEMA IF NOT EXISTS "fleet_rlm_lite"' in migration_sql
    assert 'SET LOCAL search_path TO "fleet_rlm_lite"' in migration_sql
    assert any("fleet_lite_sessions" in sql for sql in migration_sql)
    # 1 borrow on migration_pool (schema bootstrap), 2 on runtime_pool (replay cleanup + history).
    assert migration_pool.borrowed == 1
    assert runtime_pool.borrowed == 2


def test_store_open_scopes_runtime_and_migration_pools_to_the_configured_schema(monkeypatch):
    import fleet_rlm.store as store_db

    opened = []

    class CapturedPool:
        def __init__(self, dsn, **kwargs):
            self.dsn = dsn
            self.kwargs = kwargs
            self.closed = False
            opened.append(self)

        def open(self, **_kwargs):
            return None

        def close(self):
            self.closed = True

    monkeypatch.setattr(store_db, "ConnectionPool", CapturedPool)
    store = Store.open(
        "postgresql://runtime@db.example/fleet",
        migration_dsn="postgresql://migrator@db.example/fleet",
        schema="fleet_rlm_lite",
    )

    assert [pool.dsn for pool in opened] == [
        "postgresql://runtime@db.example/fleet",
        "postgresql://migrator@db.example/fleet",
    ]
    assert all(pool.kwargs["kwargs"]["options"] == "-c search_path=fleet_rlm_lite" for pool in opened)
    # Pooled connections are recycled before a one-hour Lakebase credential expires and checked on checkout.
    for pool in opened:
        assert pool.kwargs["max_lifetime"] < 60 * 60 and pool.kwargs["max_idle"] <= pool.kwargs["max_lifetime"]
        assert callable(pool.kwargs["check"])
    assert store.migration_pool is opened[1]
    assert store.connection_kwargs["options"] == "-c search_path=fleet_rlm_lite"
    store.close_migration_pool()
    assert store.migration_pool is store.pool
    assert opened[1].closed and not opened[0].closed
    store.close()
    assert opened[0].closed


def test_schema_bootstrap_does_not_rerun_an_applied_migration():
    from fleet_rlm.store import SCHEMA_VERSION

    store, pool = make_store([], [], [(SCHEMA_VERSION,)])
    store.initialize()
    statements = [statement for statement, _ in pool.conn.statements]
    assert statements[0] == "BEGIN"
    assert any("pg_advisory_xact_lock" in sql for sql in statements)
    assert "SELECT MAX(version) FROM fleet_lite_schema_migrations" in statements
    assert not any("CREATE TABLE IF NOT EXISTS fleet_lite_sessions" in sql for sql in statements)
    assert not any("INSERT INTO fleet_lite_schema_migrations(version)" in sql for sql in statements)
    assert statements[-1] == "COMMIT"


def test_memories_return_iso_timestamps_without_reading_workspace_files():
    updated = dt.datetime(2026, 10, 15, tzinfo=dt.UTC)
    memory = {
        "memory_id": "m1",
        "content": "c",
        "kind": "observation",
        "sources": {"files/a": "old"},
        "reviewed": True,
        "updated_at": updated,
    }
    store, pool = make_store([memory])
    [row] = store.memories("s1", "", reviewed_only=True)
    assert row["updated_at"] == updated.isoformat() and "stale" not in row
    assert not any("fleet_lite_vfs_files" in statement for statement, _ in pool.conn.statements)


def test_load_session_reads_history_without_recreating_the_runtime_session():
    turns = [{"turn_id": "t1", "task": "question", "answer": "answer"}]
    store, pool = make_store(
        [{"memory_summary": "known"}],
        [({"turn_id": "t1", "task": "question", "answer": "answer"},)],
    )

    assert store.load_session("s1") == ("known", turns)
    assert pool.borrowed == 1
    assert len(pool.conn.statements) == 2
    assert pool.conn.statements[0] == ("SELECT memory_summary FROM fleet_lite_sessions WHERE session_id = %s", ("s1",))
    sql_turns, params_turns = pool.conn.statements[1]
    assert (
        "FROM fleet_lite_turns WHERE session_id = %s AND status = 'completed' ORDER BY seq DESC LIMIT %s" in sql_turns
    )
    assert params_turns == ("s1", 8)


def test_committing_a_turn_is_one_transaction_with_the_completion():
    # Replies for: INSERT session, SELECT FOR UPDATE, SELECT status, UPDATE RETURNING, UPDATE session.
    store, pool = make_store([], [], [], [("turn-1",)], [])
    store.commit_turn(
        "s1",
        turn_record_json='{"turn_id":"turn-1","task":"summarize"}',
        memory_note="note",
        completion=("turn-1", {"event": "turn_complete"}),
    )
    sql = [statement for statement, _ in pool.conn.statements]
    assert pool.borrowed == 1
    assert sql[0] == "BEGIN" and sql[-1] == "COMMIT"
    assert sql[2].endswith("FOR UPDATE")
    assert not any("fleet_lite_vfs_files" in statement for statement in sql)
    update_turn = next(statement for statement in sql if statement.startswith("UPDATE fleet_lite_turns"))
    assert "status = %s" in update_turn and "record = %s::jsonb" in update_turn
    update_session = next(statement for statement in sql if statement.startswith("UPDATE fleet_lite_sessions"))
    assert "memory_summary = CASE" in update_session and "turn_count = turn_count +" in update_session


def test_interrupted_running_turns_are_failed_only_under_the_current_ownership_epoch():
    store, pool = make_store([("8",)], [("turn-old",), ("turn-stale",)])

    assert store.fail_interrupted_turns("s1", 8) == ["turn-old", "turn-stale"]

    sql = [statement for statement, _ in pool.conn.statements]
    assert sql[0] == "BEGIN"
    assert "ownership_epoch" in sql[1] and sql[1].endswith("FOR UPDATE")
    assert "WHERE session_id = %s AND status = 'running' RETURNING turn_id" in sql[2]
    payload = json.loads(pool.conn.statements[2][1][0])
    assert payload["outcome"] == "failed" and "not replayed" in payload["error"]
    assert sql[-1] == "COMMIT"


def test_stale_ownership_epoch_cannot_finalize_a_turn():
    store, pool = make_store([("9",)])

    with pytest.raises(RuntimeError, match="stale Turn write was fenced"):
        store.finish_turn("s1", "turn-old", "failed", {"event": "error"}, ownership_epoch=8)

    assert not any(statement.startswith("UPDATE fleet_lite_turns") for statement, _ in pool.conn.statements)


def test_stale_ownership_epoch_cannot_claim_a_turn():
    store, pool = make_store([("9",)])

    with pytest.raises(RuntimeError, match="stale Turn write was fenced"):
        store.claim_turn("s1", "turn-old", "key", "digest", ownership_epoch=8)

    assert not any(statement.startswith("INSERT INTO fleet_lite_turns") for statement, _ in pool.conn.statements)


@pytest.mark.parametrize("found", [False, True])
def test_existing_turn_receipt_lookup_is_session_scoped_and_read_only(found):
    receipt = {"turn_id": "original", "task_sha256": "digest", "status": "completed", "result": {"answer": "saved"}}
    store, pool = make_store([receipt] if found else [])
    assert store.turn_by_key("s1", "key") == (receipt if found else None)
    assert pool.conn.statements == [
        (
            "SELECT turn_id, task_sha256, status, result "
            "FROM fleet_lite_turns WHERE session_id = %s AND idempotency_key = %s",
            ("s1", "key"),
        )
    ]


def test_stale_ownership_epoch_cannot_commit_a_turn_record():
    store, pool = make_store([], [("s1",)], [("9",)])

    with pytest.raises(RuntimeError, match="stale Turn write was fenced"):
        store.commit_turn("s1", turn_record_json='{"turn_id":"turn-old"}', ownership_epoch=8)

    assert not any(
        statement.startswith("UPDATE fleet_lite_turns") or statement.startswith("INSERT INTO fleet_lite_turns")
        for statement, _ in pool.conn.statements
    )


def test_selected_replay_snapshot_commits_atomically_with_turn_completion():
    from fleet_rlm.replay import split_snapshot_for_storage

    content = b"immutable input"
    digest = hashlib.sha256(content).hexdigest()
    _, blobs = split_snapshot_for_storage(digest, content)
    snapshot = (digest, content)
    # Replies: INSERT session, SELECT FOR UPDATE, SELECT status, UPDATE turn RETURNING,
    # UPDATE session, N blob inserts, INSERT replay_snapshot, SELECT sha256.
    store, pool = make_store([], [], [], [("t1",)], [], *([] for _ in blobs), [], [(digest,)])
    store.commit_turn(
        "s1",
        turn_record_json='{"turn_id":"t1","replay_snapshot_selected":true}',
        completion=("t1", {"event": "turn_complete"}),
        replay_snapshot=snapshot,
    )
    sql = [statement for statement, _ in pool.conn.statements]
    assert sql[0] == "BEGIN" and sql[-1] == "COMMIT"
    assert any(statement.startswith("INSERT INTO fleet_lite_replay_blobs") for statement in sql)
    assert any(statement.startswith("INSERT INTO fleet_lite_replay_snapshots") for statement in sql)
    assert any(statement.startswith("UPDATE fleet_lite_turns") for statement in sql)
    assert sql.index(next(s for s in sql if s.startswith("UPDATE fleet_lite_turns"))) < sql.index(
        next(s for s in sql if s.startswith("INSERT INTO fleet_lite_replay_snapshots"))
    )


SNAPSHOT_PROVENANCE = {
    "snapshot_name": "fleet-rlm-python313-v10",
    "snapshot_id": "snap-provider-1",
    "snapshot_cpu": 4,
    "snapshot_mem": 8,
    "snapshot_disk": 8,
    "snapshot_definition_digest": "b" * 64,
    "snapshot_build_digest": "c" * 64,
}


def test_snapshot_provenance_round_trips_into_the_turn_status_payload():
    calls = []
    store = SimpleNamespace(commit_turn=lambda *args, **kwargs: calls.append((args, kwargs)))
    turn_record = {"turn_id": "t1", "answer": "5", "route": "flex", **SNAPSHOT_PROVENANCE}
    _runtime(store, []).commit_turn("s1", turn_record)
    (session_id,), kwargs = calls[0]
    assert session_id == "s1"
    turn_id, result = kwargs["completion"]
    assert turn_id == "t1"
    assert {key: result[key] for key in SNAPSHOT_PROVENANCE} == SNAPSHOT_PROVENANCE
    stored_record = json.loads(kwargs["turn_record_json"])
    assert {key: stored_record[key] for key in SNAPSHOT_PROVENANCE} == SNAPSHOT_PROVENANCE


@pytest.mark.parametrize(
    ("field", "value"),
    [("cleanup", "not_required"), ("root_sandbox_id", "sandbox-7")],
)
def test_turn_metadata_round_trips_into_the_stored_turn_and_status_payload(field, value):
    calls = []
    store = SimpleNamespace(commit_turn=lambda *args, **kwargs: calls.append((args, kwargs)))
    turn_record = {"turn_id": "t1", "answer": "5", "route": "flex", field: value}
    _runtime(store, []).commit_turn("s1", turn_record)

    turn_id, result = calls[0][1]["completion"]
    assert turn_id == "t1"
    assert result[field] == value
    assert json.loads(calls[0][1]["turn_record_json"])[field] == value


def test_one_turn_joins_its_status_payload_to_the_stored_record_with_null_preserved():
    calls = []
    store = SimpleNamespace(commit_turn=lambda *args, **kwargs: calls.append((args, kwargs)))
    turn_record = {
        "turn_id": "t1",
        "answer": "5",
        "route": "flex",
        "outcome": "completed",
        "deadline_source": "request",
        "effective_seconds": None,
        "duration_seconds": 12.5,
        "cleanup": "not_required",
        **SNAPSHOT_PROVENANCE,
    }
    _runtime(store, []).commit_turn("s1", turn_record)

    turn_id, result = calls[0][1]["completion"]
    assert turn_id == "t1"
    joined = (
        "route",
        "answer",
        "outcome",
        "deadline_source",
        "effective_seconds",
        "duration_seconds",
        "cleanup",
        *SNAPSHOT_PROVENANCE,
    )
    assert {key: result[key] for key in joined} == {key: turn_record[key] for key in joined}
    assert result["effective_seconds"] is None, "a deadline-free Turn keeps an explicit null"
    assert json.loads(calls[0][1]["turn_record_json"]) == turn_record


def test_provider_bounds_round_trip_into_the_stored_turn_and_status_payload():
    calls = []
    store = SimpleNamespace(commit_turn=lambda *args, **kwargs: calls.append((args, kwargs)))
    bounds = {
        "ttl_minutes": 1440,
        "auto_stop_interval": 60,
        "maximum_lifespan_minutes": None,
        "provider_owned": True,
    }
    turn_record = {"turn_id": "t1", "answer": "5", "route": "flex", **bounds}
    _runtime(store, []).commit_turn("s1", turn_record)

    turn_id, result = calls[0][1]["completion"]
    assert turn_id == "t1"
    assert {key: result[key] for key in bounds} == bounds
    assert {key: json.loads(calls[0][1]["turn_record_json"])[key] for key in bounds} == bounds


def test_snapshot_provenance_round_trips_through_the_immutable_replay():
    import base64

    from fleet_rlm.replay import (
        decode_snapshot,
        encode_snapshot,
        rehydrate_snapshot_from_storage,
        split_snapshot_for_storage,
    )

    payload = b"hello world"
    payload_sha = hashlib.sha256(payload).hexdigest()
    encoded_payload = base64.b64encode(payload).decode("ascii")
    snapshot = {
        "schema_version": 1,
        "session_id": "s1",
        "turn_id": "t1",
        "task": "immutable task",
        "workspace_manifest": "/workspace/files/data.txt",
        "context_files": {"files/data.txt": payload_sha},
        "replay_files": {"files/data.txt": encoded_payload},
        "skill_revision_sha256": hashlib.sha256(b"{}").hexdigest(),
        **SNAPSHOT_PROVENANCE,
    }
    digest, compressed = encode_snapshot(snapshot)
    _, blobs = split_snapshot_for_storage(digest, compressed)
    store, pool = make_store([], [], [], [("t1",)], [], *([] for _ in blobs), [], [(digest,)])
    store.commit_turn(
        "s1",
        turn_record_json=json.dumps(
            {
                "turn_id": "t1",
                "replay_snapshot_selected": True,
                "replay_snapshot_sha256": digest,
                **SNAPSHOT_PROVENANCE,
            }
        ),
        completion=("t1", {"event": "turn_complete", **SNAPSHOT_PROVENANCE}),
        replay_snapshot=(digest, compressed),
    )
    blob_map = {
        params[0]: params[2]
        for statement, params in pool.conn.statements
        if statement.startswith("INSERT INTO fleet_lite_replay_blobs")
    }
    snapshot_params = next(
        params
        for statement, params in pool.conn.statements
        if statement.startswith("INSERT INTO fleet_lite_replay_snapshots")
    )
    manifest = json.loads(snapshot_params[3])
    rehydrated_digest, rehydrated_compressed = rehydrate_snapshot_from_storage(snapshot_params[2], manifest, blob_map)
    decoded = decode_snapshot(rehydrated_digest, rehydrated_compressed)
    assert {key: decoded[key] for key in SNAPSHOT_PROVENANCE} == SNAPSHOT_PROVENANCE
    assert decoded["replay_files"] == {"files/data.txt": encoded_payload}


@pytest.mark.parametrize(
    ("deadline_fields", "expected_state"),
    [({}, "pre-provenance"), ({"deadline_source": "default", "effective_seconds": 900}, "unresolved")],
)
def test_replay_without_snapshot_provenance_is_readable(deadline_fields, expected_state):
    from fleet_rlm.replay import decode_snapshot, encode_snapshot, selected_snapshot

    historical = {
        "schema_version": 1,
        "session_id": "s1",
        "turn_id": "t1",
        "task": "recorded before provenance existed",
        "workspace_manifest": "/workspace/files (empty)",
        "context_files": {},
        "replay_files": {},
        "skill_revision_sha256": hashlib.sha256(b"{}").hexdigest(),
    }
    historical.update(deadline_fields)
    digest, compressed = encode_snapshot(historical)
    decoded = decode_snapshot(digest, compressed)
    assert decoded == historical
    assert not set(SNAPSHOT_PROVENANCE) & set(decoded), "reading must not backfill provenance"

    store = SimpleNamespace(replay_snapshot=lambda _session, _turn: (digest, compressed))
    loaded = selected_snapshot(store, "s1", "t1", digest)
    assert loaded == {**historical, "provenance_state": expected_state}
    assert not set(SNAPSHOT_PROVENANCE) & set(loaded), "reading must not backfill provenance"

    review_store, _pool = make_store([(1,)])
    review_store.replay_snapshot = lambda *_args: (digest, compressed)
    review_store.save_review_labels(
        "s1",
        "t1",
        digest,
        review_status="approved",
        source_group="group",
        expected_answer="5",
        expected_values=["5"],
        route="flex",
        critical=True,
        reviewer="human",
    )
    assert encode_snapshot(historical) == (digest, compressed)


def _runtime(store: Any, files: list[str]) -> SessionRuntime:
    sandbox = SimpleNamespace(session_id="s1", list_workspace_files=lambda: list(files))
    return SessionRuntime(sandbox_mgr=sandbox, store=store, skill_mgr=None, engine=None)


@pytest.mark.parametrize("paths", [["../escape"], ["outside/a"], ["files/a\\b"], ["files/a\nb"], [1], {}, "", None])
def test_runtime_rejects_an_invalid_workspace_listing_before_any_sql_runs(paths):
    store, pool = make_store()
    runtime = _runtime(store, [])
    runtime.sandbox_mgr.list_workspace_files = lambda: paths
    with pytest.raises(ValueError, match="invalid relative path"):
        runtime.commit_turn("s1", {"turn_id": "t1"})
    assert pool.borrowed == 0


def test_runtime_reports_artifacts_and_records_the_completion_without_reading_files():
    calls = []
    store = SimpleNamespace(commit_turn=lambda *args, **kwargs: calls.append((args, kwargs)))
    usage = {
        "lm_calls": 4,
        "sandbox_errors": 1,
        "submit_rejections": 2,
        "budget_breakdown": {"generation_query": 3, "routing": 1, "skill_selection": 0, "other_decisions": 0},
    }
    completion = _runtime(store, ["files/in.txt", "artifacts/out.txt"]).commit_turn(
        "s1", {"turn_id": "t1", "answer": "a", "route": "flex", "trace_id": "tr-1", **usage}
    )
    assert completion["artifacts"] == ["artifacts/out.txt"]
    (session_id,), kwargs = calls[0]
    assert session_id == "s1"
    turn_id, result = kwargs["completion"]
    assert completion == result and completion["turn_id"] == turn_id
    assert turn_id == "t1" and result["trace_id"] == "tr-1" and result["artifacts"] == ["artifacts/out.txt"]
    assert json.loads(kwargs["turn_record_json"])["route"] == "flex"
    assert {key: result[key] for key in usage} == usage


def test_claim_turn_refuses_a_key_reused_for_another_task():
    prior = {"turn_id": "t0", "task_sha256": "other", "status": "running", "result": {}}
    store = SimpleNamespace(claim_turn=lambda *_args: prior)
    with pytest.raises(IdempotencyConflictError, match="another task"):
        _runtime(store, []).claim_turn("t1", "key", "task")


def test_a_dropped_idle_connection_is_retried_once_after_a_pause(monkeypatch):
    import psycopg

    import fleet_rlm.store as store_db

    paused: list[float] = []
    monkeypatch.setattr(store_db, "_RETRY_DELAY_SECONDS", 0.25)
    monkeypatch.setattr(store_db.time, "sleep", paused.append)
    store, pool = make_store([({"mode": "flex"},)])
    calls = []
    real = pool.connection

    @contextmanager
    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        with real() as connection:
            yield connection

    pool.connection = flaky
    assert store.runtime_settings("s1") == {"mode": "flex"}
    assert len(calls) == 2 and paused == [0.25]


def test_lakebase_connection_class_caches_oauth_token_until_near_expiry(monkeypatch):

    import psycopg

    now = 1_000_000.0
    monkeypatch.setattr("fleet_rlm.store.leases.time.monotonic", lambda: now)
    monkeypatch.setattr("fleet_rlm.store.leases.time.time", lambda: now)

    credentials = iter(
        (
            SimpleNamespace(token="token-one", expiration_time=now + 3_600.0),
            SimpleNamespace(token="token-two", expiration_time=now + 3_600.0),
        )
    )
    gen_calls: list[str] = []
    calls = []

    def generate(*, endpoint: str):
        gen_calls.append(endpoint)
        return next(credentials)

    client = SimpleNamespace(postgres=SimpleNamespace(generate_database_credential=generate))

    def connect(connection_class, conninfo="", **kwargs):
        calls.append((connection_class, conninfo, kwargs))
        return kwargs

    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(connect))
    connection_class = lakebase_connection_class(client, "projects/p/branches/b/endpoints/e")
    first = connection_class.connect("host=lakebase", user="fleet_app")
    second = connection_class.connect("host=lakebase", user="fleet_app")
    assert first["password"] == "token-one" and second["password"] == "token-one"
    assert len(gen_calls) == 1

    # Advance within the 300s refresh margin to force credential refresh.
    now = 1_000_000.0 + 3_350.0
    third = connection_class.connect("host=lakebase", user="fleet_app")
    assert third["password"] == "token-two"
    assert len(gen_calls) == 2
    assert len(calls) == 3
    assert all(call[0] is connection_class and call[1] == "host=lakebase" for call in calls)


def test_global_settings_round_trip_by_scope_and_default_to_empty():
    store, pool = make_store([({"auto_run": True},)], [])
    assert store.global_settings("improve") == {"auto_run": True}
    assert store.global_settings("missing") == {}
    store.update_global_settings("improve", {"min_new_labels": 3, "max_metric_calls": None})
    sql, params = pool.conn.statements[-1]
    # Merged in SQL, so concurrent updates touch only their own keys; None removes a key.
    assert "settings = (fleet_lite_global_settings.settings - %s::text[]) || EXCLUDED.settings" in sql
    assert params == ("improve", '{"min_new_labels": 3}', ["max_metric_calls"])


def test_commit_turn_requires_a_turn_id_so_a_connection_retry_cannot_duplicate_the_record():
    store, pool = make_store()
    with pytest.raises(ValueError, match="turn_id"):
        store.commit_turn("s1", turn_record_json='{"answer":"42"}')
    assert pool.borrowed == 0


def test_runtime_rejects_a_turn_record_without_a_turn_id_before_any_sql_runs():
    calls: list = []
    store = SimpleNamespace(commit_turn=lambda *args, **kwargs: calls.append((args, kwargs)))
    with pytest.raises(ValueError, match="turn_id"):
        _runtime(store, []).commit_turn("s1", {"answer": "42"})
    assert calls == []


def test_improve_queue_joins_turns_across_sessions_with_optional_labels():
    store, pool = make_store([{"turn_id": "t1"}])
    assert store.improve_queue(25) == [{"turn_id": "t1"}]
    sql, params = pool.conn.statements[0]
    assert "FROM fleet_lite_replay_snapshots AS replay" in sql and "LEFT JOIN fleet_lite_review_labels" in sql
    assert "WHERE replay.session_id" not in sql and "turns.status = 'completed'" in sql
    assert params == (25, 0)


def test_turn_reference_resolves_a_turn_id_to_its_session_trace_and_snapshot():
    store, pool = make_store([{"session_id": "s1", "turn_id": "t1", "trace_id": "tr-1"}])
    assert store.turn_reference("t1")["trace_id"] == "tr-1"
    sql, params = pool.conn.statements[0]
    assert "turns.result->>'trace_id' AS trace_id" in sql and "replay.turn_id = %s" in sql
    assert params == ("t1",)


# History sanitization


def test_history_is_bounded_and_keeps_the_newest_turns_in_order() -> None:
    turns = [{"task": f"task-{index}", "answer": f"answer-{index}"} for index in range(10)]

    history = SessionRuntime._render_history(turns)

    assert "task-0" not in history
    assert history.index("task-2") < history.index("task-9")
    assert len(history) <= 16_000

    oversized = SessionRuntime._render_history([{"task": "task-" + "x" * 50_000, "answer": "tail" * 20_000}])
    assert len(oversized) <= 16_000
    assert oversized.endswith("tail")


# Optimization records and leases


def run_row(**changes: Any) -> dict[str, Any]:
    row = {
        "run_id": "run-1",
        "request_id": "request-1",
        "kind": "optimize",
        "options": {"max_metric_calls": 20},
        "owner_id": "worker-1",
        "managed": True,
        "status": "running",
        "stage": "search",
        "stop_requested": False,
        "metadata": {"candidate_version": "candidate-1"},
        "created_at": datetime(2026, 10, 26, tzinfo=UTC),
        "updated_at": datetime(2026, 10, 26, tzinfo=UTC),
    }
    return {**row, **changes}


def test_create_optimization_run_records_active_identity_and_returns_metadata():
    store, pool = make_store([run_row()])

    record = store.create_optimization_run("run-1", "request-1", {"max_metric_calls": 20}, "worker-1", managed=True)

    assert record["run_id"] == "run-1" and record["kind"] == "optimize"
    assert record["status"] == "running" and record["stage"] == "search"
    assert record["candidate_version"] == "candidate-1"
    assert record["created_at"] == "2026-10-26T00:00:00+00:00"
    sql, params = pool.conn.statements[1]
    assert "ON CONFLICT (request_id) DO NOTHING RETURNING *" in sql
    assert "jsonb_build_object('trigger', %s::text)" in sql  # recorded in the row's own insert
    assert params == ("run-1", "request-1", "optimize", '{"max_metric_calls":20}', "worker-1", True, "manual")


def test_create_optimization_run_refuses_an_unknown_kind_before_sql():
    store, pool = make_store()

    with pytest.raises(ValueError, match="job kind"):
        store.create_optimization_run("run-1", "request-1", {}, "worker-1", kind="deploy")
    assert pool.borrowed == 0


def test_create_optimization_run_is_idempotent_for_the_same_request_and_details():
    existing = run_row()
    store, pool = make_store([], [existing])

    record = store.create_optimization_run(
        "new-generated-id", "request-1", {"max_metric_calls": 20}, "worker-1", managed=True
    )

    assert record["run_id"] == "run-1"
    assert pool.borrowed == 1
    assert len(pool.conn.statements) == 4
    assert "FOR UPDATE" in pool.conn.statements[2][0]


def test_create_optimization_run_rejects_reused_request_with_different_options():
    store, pool = make_store([], [run_row()])

    with pytest.raises(ValueError, match="different run details"):
        store.create_optimization_run("run-2", "request-1", {"max_metric_calls": 21}, "worker-1", managed=True)
    assert pool.borrowed == 1


def test_create_optimization_run_maps_global_active_unique_violation_to_busy():
    store, pool = make_store(error=psycopg.errors.UniqueViolation("active run exists"))

    with pytest.raises(ValueError, match="already active"):
        store.create_optimization_run("run-2", "request-2", {}, "worker-2")
    assert pool.conn.statements[1][0].startswith("INSERT INTO fleet_lite_optimization_runs")


def test_get_and_list_optimization_runs_are_global_and_canonical():
    completed = run_row(status="completed", metadata={"candidate_version": "candidate-1", "status": "spoofed"})
    store, pool = make_store([completed], [completed])

    one = store.get_optimization_run("run-1")
    assert one is not None and one["status"] == "completed"  # Ledger columns override metadata.
    assert store.list_optimization_runs()[0]["candidate_version"] == "candidate-1"
    assert pool.borrowed == 2
    assert "WHERE run_id = %s" in pool.conn.statements[0][0]
    assert "ORDER BY updated_at DESC" in pool.conn.statements[1][0]


def test_a_legacy_run_keeps_its_session_only_as_metadata():
    legacy = run_row(metadata={"legacy_session_id": "s1"})
    store, _pool = make_store([legacy])

    record = store.get_optimization_run("run-1")

    assert record is not None and record["legacy_session_id"] == "s1" and "session_id" not in record


def test_tuple_rows_follow_the_migrated_column_order():
    row = run_row()
    columns = (
        "run_id", "request_id", "kind", "options", "owner_id", "managed", "status", "stage",
        "stop_requested", "metadata", "created_at", "updated_at",
    )  # fmt: skip
    store, _pool = make_store([tuple(row[name] for name in columns)])

    record = store.get_optimization_run("run-1")

    assert record is not None and record["kind"] == "optimize" and record["run_id"] == "run-1"


def test_update_optimization_run_fences_owner_and_expected_status():
    store, pool = make_store([run_row(stage="validation", metadata={"candidate_version": "candidate-1", "score": 0.8})])

    record = store.update_optimization_run(
        "run-1", {"stage": "validation", "score": 0.8}, owner_id="worker-1", expected_statuses=("running",)
    )

    assert record is not None and record["stage"] == "validation" and record["score"] == 0.8
    statement, params = pool.conn.statements[0]
    assert "owner_id=%s" in statement and "status=ANY(%s::text[])" in statement
    assert params[7] == "run-1" and params[-2:] == (["running"], ["running"])


def test_completed_update_atomically_yields_to_a_previously_accepted_stop_request():
    cancelled = run_row(
        status="cancelled",
        stage="cancelled",
        stop_requested=True,
        metadata={"candidate_version": None, "finished_at": "2026-10-26T12:00:00+00:00"},
    )
    store, pool = make_store([cancelled])

    record = store.update_optimization_run(
        "run-1",
        {
            "status": "completed",
            "stage": "completed",
            "candidate_version": "run-1",
            "finished_at": "2026-10-26T12:00:00+00:00",
        },
        owner_id="worker-1",
        expected_statuses=("running",),
    )

    assert record is not None
    assert record["status"] == "cancelled" and record["stage"] == "cancelled"
    assert record["stop_requested"] is True and record["candidate_version"] is None
    statement, params = pool.conn.statements[0]
    assert "status=CASE WHEN %s AND stop_requested THEN 'cancelled'" in statement
    assert "stage=CASE WHEN %s AND stop_requested THEN 'cancelled'" in statement
    assert "THEN '{\"candidate_version\":null}'::jsonb" in statement
    assert params[:7] == (
        True,
        "completed",
        True,
        "completed",
        None,
        '{"candidate_version":"run-1","finished_at":"2026-10-26T12:00:00+00:00"}',
        True,
    )


@pytest.mark.parametrize("key", ["run_id", "request_id", "kind", "options", "owner_id", "managed"])
def test_update_optimization_run_rejects_identity_changes_before_sql(key: str):
    store, pool = make_store()

    with pytest.raises(ValueError, match="identity fields"):
        store.update_optimization_run("run-1", {key: "changed"})
    assert pool.borrowed == 0


def test_update_optimization_run_returns_none_when_a_fence_does_not_match():
    store, _pool = make_store([])
    assert store.update_optimization_run("run-1", {"stage": "done"}, owner_id="stale-worker") is None


def test_update_optimization_run_maps_active_status_collision_to_busy():
    store, _pool = make_store(error=psycopg.errors.UniqueViolation("active run exists"))

    with pytest.raises(ValueError, match="Another optimization run is active"):
        store.update_optimization_run("run-1", {"status": "running"})


def test_request_optimization_stop_is_idempotent_for_running_work():
    store, pool = make_store([run_row(stop_requested=True)])

    record = store.request_optimization_stop("run-1")

    assert record["status"] == "running" and record["stop_requested"] is True
    assert "status = 'running' RETURNING *" in pool.conn.statements[0][0]


def test_request_optimization_stop_refuses_a_terminal_run():
    store, _pool = make_store([], [run_row(status="completed")])

    with pytest.raises(ValueError, match="only a running"):
        store.request_optimization_stop("run-1")


def test_claim_optimization_review_claims_completed_candidate_and_recovers_approving_owner():
    store, pool = make_store([run_row(status="completed")], [run_row(status="approving", owner_id="reviewer-1")])
    claimed = store.claim_optimization_review("run-1", "reviewer-1")
    assert claimed["status"] == "approving" and claimed["owner_id"] == "reviewer-1"
    assert "status IN ('completed', 'approving') RETURNING *" in pool.conn.statements[2][0]

    store, _pool = make_store(
        [run_row(status="approving", owner_id="dead-reviewer")],
        [run_row(status="approving", owner_id="new-reviewer")],
    )
    recovered = store.claim_optimization_review("run-1", "new-reviewer")
    assert recovered["status"] == "approving" and recovered["owner_id"] == "new-reviewer"


def test_claim_optimization_review_maps_another_active_run_to_busy():
    store, _pool = make_store(error=psycopg.errors.UniqueViolation("active run exists"))

    with pytest.raises(ValueError, match="Another optimization run is active"):
        store.claim_optimization_review("run-1", "reviewer-1")


def test_reject_optimization_run_keeps_candidate_metadata_and_records_decision():
    store, pool = make_store(
        [run_row(status="rejected", metadata={"candidate_version": "candidate-1", "decision": "rejected"})]
    )

    rejected = store.reject_optimization_run("run-1")

    assert rejected["status"] == "rejected" and rejected["candidate_version"] == "candidate-1"
    assert "metadata = metadata || CASE WHEN status = 'rejected'" in pool.conn.statements[0][0]


def test_optimization_lease_uses_pooled_leases_table_and_distinct_lock_scope():
    store, pool = make_store([(1,)], [])
    try:
        held = store.acquire_optimization_lease(owner_id="worker-1")
        assert held.scope == _OPTIMIZATION_LOCK_SCOPE
        assert held.kind == "global"
        assert held.owner_id == "worker-1"
        assert held.epoch == 1
        sql_stmt, params = pool.conn.statements[0]
        assert "INSERT INTO fleet_lite_leases" in sql_stmt
        assert params[0] == _OPTIMIZATION_LOCK_SCOPE
        assert params[1] == "worker-1"

        with pytest.raises(RuntimeError, match="already owned"):
            store.acquire_optimization_lease(owner_id="worker-2")

        held.close()
        assert held.closed is True
        delete_sql, delete_params = pool.conn.statements[-1]
        assert "DELETE FROM fleet_lite_leases" in delete_sql
        assert delete_params == (_OPTIMIZATION_LOCK_SCOPE, "worker-1", 1)
    finally:
        store.heartbeat.stop()


def test_owner_liveness_probe_uses_pooled_lease_table_and_releases_on_close():
    store, pool = make_store([(1,)], [(1,)], [(1,)], [], [], [])
    try:
        owner = store.acquire_optimization_owner_lease("api-1")
        worker = store.acquire_optimization_lease()
        assert owner.scope == "fleet:optimization-owner:api-1"
        assert worker.scope == _OPTIMIZATION_LOCK_SCOPE
        assert store.optimization_owner_alive("api-1") is True
        assert store.optimization_owner_alive("api-2") is False
        owner.close()
        assert owner.closed is True
        assert store.optimization_owner_alive("api-1") is False
        worker.close()
        assert worker.closed is True
        assert pool.borrowed == 7
    finally:
        store.heartbeat.stop()


def test_owner_probe_does_not_treat_database_errors_as_owner_death():
    store, _pool = make_store(error=psycopg.errors.OperationalError("database unavailable"))
    with pytest.raises(psycopg.errors.OperationalError):
        store.optimization_owner_alive("api-1")


def test_heartbeat_batches_session_and_global_lease_renewals_and_handles_transient_errors(monkeypatch):
    store, pool = make_store(
        [("s1",)],
        [(_OPTIMIZATION_LOCK_SCOPE,)],
        [(_OPTIMIZATION_LOCK_SCOPE,)],
        [(_OPTIMIZATION_LOCK_SCOPE,)],
    )
    now = {"value": 100.0}
    monkeypatch.setattr("fleet_rlm.store.leases.time.monotonic", lambda: now["value"])
    lost_events: list[str] = []
    idle_calls: list[int] = []

    store.heartbeat.register_idle_callback(lambda: idle_calls.append(1))
    with store.heartbeat._lock:
        store.heartbeat._sessions["s1"] = (1, "w1", lambda: lost_events.append("s1"), 100.0)
        store.heartbeat._sessions["s2"] = (2, "w1", lambda: lost_events.append("s2"), 100.0)
        store.heartbeat._leases[_OPTIMIZATION_LOCK_SCOPE] = (3, "w1", lambda: lost_events.append("opt"), 100.0)

    store.heartbeat.tick()
    assert lost_events == ["s2"]
    assert idle_calls == [1]
    assert "UPDATE fleet_lite_sessions AS s" in pool.conn.statements[0][0]
    assert "UPDATE fleet_lite_leases AS l" in pool.conn.statements[1][0]

    # Transient operational error keeps lease alive before TTL expires, then expires after TTL.
    pool.conn.error = psycopg.errors.OperationalError("brief glitch")
    now["value"] = 120.0
    store.heartbeat.tick()
    assert lost_events == ["s2"]

    pool.conn.error = psycopg.errors.OperationalError("still disconnected")
    now["value"] = 165.0
    store.heartbeat.tick()
    assert lost_events == ["s2", "s1"]


def test_cleanup_expired_replays_preserves_gold_labeled_snapshots_and_prunes_orphan_blobs():
    store, pool = make_store([("s1",), ("s2",)], [("b1",)])

    deleted_snapshots, deleted_blobs = store.cleanup_expired_replays(retention_days=14, batch_limit=25)

    assert (deleted_snapshots, deleted_blobs) == (2, 1)
    snap_sql, snap_params = pool.conn.statements[1]
    blob_sql, blob_params = pool.conn.statements[2]
    assert "DELETE FROM fleet_lite_replay_snapshots" in snap_sql
    assert "NOT EXISTS ( SELECT 1 FROM fleet_lite_review_labels AS l" in snap_sql
    assert snap_params == (14, 25)
    assert "DELETE FROM fleet_lite_replay_blobs AS b" in blob_sql
    assert blob_params == (25,)


@pytest.mark.skipif(
    not os.environ.get("FLEET_TEST_POSTGRES_URL"), reason="set FLEET_TEST_POSTGRES_URL to run against a real PostgreSQL"
)
def test_schema_v1_on_real_postgres_bootstraps_and_round_trips():
    """Opt-in: initializes schema v1 in a scratch schema and verifies idempotent re-initialization."""
    url = os.environ["FLEET_TEST_POSTGRES_URL"]
    schema = "fleet_mig_" + uuid.uuid4().hex[:8]
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
        try:
            store = Store.open(url, wait=True, schema=schema)
            try:
                store.initialize()
                store.initialize()  # idempotent: nothing pending the second time
                admin.execute(f"SET search_path TO {schema}")
                indexes = {
                    row[0] for row in admin.execute("SELECT indexname FROM pg_indexes WHERE schemaname=%s", (schema,))
                }
                assert "fleet_lite_one_active_optimization_run" in indexes
                assert "fleet_lite_optimization_runs_pkey" in indexes
                assert "fleet_lite_optimization_runs_request_id_key" in indexes
                assert "fleet_lite_memories_content_fts" in indexes
                latest_version = admin.execute("SELECT MAX(version) FROM fleet_lite_schema_migrations").fetchone()[0]
                assert latest_version == SCHEMA_VERSION
                store.update_global_settings("improve", {"auto_run": True, "min_new_labels": 3})
                store.update_global_settings("improve", {"min_new_labels": None, "max_total_tokens": 9})
                assert store.global_settings("improve") == {"auto_run": True, "max_total_tokens": 9}
                created = store.create_optimization_run("run-v1", "req-v1", {"max_metric_calls": 10}, "owner-1")
                assert created["run_id"] == "run-v1" and created["kind"] == "optimize"
            finally:
                store.close()
        finally:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")


# Opt-in live Lakebase privileges

_ENABLED = os.environ.get("FLEET_RUN_LAKEBASE_ROLE_TESTS") == "1"


@pytest.mark.skipif(not _ENABLED, reason="explicit Lakebase role-test opt-in required")
def test_fresh_lakebase_role_split_and_default_privileges() -> None:
    runtime_url = os.environ.get("FLEET_ROLE_TEST_RUNTIME_URL")
    migration_url = os.environ.get("FLEET_ROLE_TEST_MIGRATION_URL")
    if not runtime_url or not migration_url:
        pytest.skip("set both FLEET_ROLE_TEST_*_URL variables for live verification")

    runtime_info = conninfo_to_dict(runtime_url)
    migration_info = conninfo_to_dict(migration_url)
    if runtime_info.get("dbname") != migration_info.get("dbname"):
        pytest.fail("runtime and migration DSNs must target the same database")
    if runtime_info.get("host") != migration_info.get("host"):
        pytest.fail("runtime and migration DSNs must target the same endpoint")
    if runtime_info.get("dbname") != "fleet_rlm_lite":
        pytest.fail("refusing live role test outside the fresh fleet_rlm_lite database")

    schema = "fleet_rlm_lite"
    table_name = f"_fleet_role_check_{uuid.uuid4().hex}"
    qualified_table = sql.Identifier(schema, table_name)

    with psycopg.connect(migration_url, autocommit=True) as migration:
        migration_role = migration.execute("SELECT current_user").fetchone()[0]
        if migration_role != "fleet_migrator":
            pytest.fail("migration DSN must authenticate as fleet_migrator")
        migration.execute(
            sql.SQL("CREATE TABLE {} (id integer PRIMARY KEY, value text NOT NULL)").format(qualified_table)
        )
        try:
            with psycopg.connect(runtime_url, autocommit=True) as runtime:
                runtime_role = runtime.execute("SELECT current_user").fetchone()[0]
                if runtime_role != "fleet_app":
                    pytest.fail("runtime DSN must authenticate as fleet_app")

                usage, create = runtime.execute(
                    "SELECT has_schema_privilege(current_user, %s, 'USAGE'), "
                    "has_schema_privilege(current_user, %s, 'CREATE')",
                    (schema, schema),
                ).fetchone()
                assert usage is True
                assert create is False

                has_dml = runtime.execute(
                    "SELECT bool_and(has_table_privilege(current_user, %s, privilege)) "
                    "FROM unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']) AS privilege",
                    (f"{schema}.{table_name}",),
                ).fetchone()[0]
                assert has_dml is True

                with pytest.raises(InsufficientPrivilege):
                    runtime.execute(
                        sql.SQL("CREATE TABLE {} (id integer)").format(sql.Identifier(schema, f"{table_name}_denied"))
                    )

                runtime.execute(sql.SQL("INSERT INTO {} (id, value) VALUES (1, 'ok')").format(qualified_table))
                runtime.execute(sql.SQL("UPDATE {} SET value = 'updated' WHERE id = 1").format(qualified_table))
                assert runtime.execute(sql.SQL("SELECT value FROM {}").format(qualified_table)).fetchone() == (
                    "updated",
                )
                runtime.execute(sql.SQL("DELETE FROM {} WHERE id = 1").format(qualified_table))
        finally:
            migration.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(qualified_table))
