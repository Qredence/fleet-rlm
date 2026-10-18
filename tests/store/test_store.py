"""The Store runs every SQL statement over one pool; the harness validates before anything is written."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.store import IdempotencyConflictError, Store
from fleet_rlm.store.db import (
    _SCHEMA_MIGRATIONS,
    SCHEMA_STATEMENTS,
    SCHEMA_VERSION,
    VolumePostgresHarness,
    lakebase_connection_class,
)


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeCursor:
        rendered = sql.as_string() if hasattr(sql, "as_string") else sql
        self.connection.statements.append((" ".join(rendered.split()), params))
        self.rows = self.connection.replies.pop(0) if self.connection.replies else []
        return self

    def fetchall(self) -> list[Any]:
        return list(self.rows)

    def fetchone(self) -> Any:
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self, replies: list[list[Any]]) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.replies = replies

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeCursor:
        return FakeCursor(self).execute(sql, params)

    def cursor(self, **_kwargs: Any) -> FakeCursor:
        return FakeCursor(self)

    @contextmanager
    def transaction(self):
        self.statements.append(("BEGIN", ()))
        yield
        self.statements.append(("COMMIT", ()))


class FakePool:
    def __init__(self, *replies: list[Any]) -> None:
        self.conn = FakeConnection(list(replies))
        self.borrowed = 0

    @contextmanager
    def connection(self):
        self.borrowed += 1
        yield self.conn


def make_store(*replies: list[Any]) -> tuple[Store, FakePool]:
    pool = FakePool(*replies)
    return Store(pool, "postgresql://fake"), pool  # ty: ignore[invalid-argument-type]


def test_schema_keeps_the_lite_tables_separate_from_the_legacy_schema():
    session_schema, vfs_schema = SCHEMA_STATEMENTS[:2]
    assert "CREATE TABLE IF NOT EXISTS fleet_lite_sessions" in session_schema
    assert "CREATE TABLE IF NOT EXISTS fleet_lite_vfs_files" in vfs_schema
    assert "REFERENCES fleet_lite_sessions(session_id)" in vfs_schema
    assert "CREATE TABLE IF NOT EXISTS fleet_sessions" not in session_schema
    replay_schema = next(statement for statement in SCHEMA_STATEMENTS if "fleet_lite_replay_snapshots" in statement)
    assert "REFERENCES fleet_lite_turns(session_id, turn_id) ON DELETE CASCADE" in replay_schema


def test_migration_four_retires_the_file_mirror_without_deleting_rows():
    assert SCHEMA_VERSION == 4
    version, statements = _SCHEMA_MIGRATIONS[-1]
    assert version == 4
    assert statements == ("ALTER TABLE IF EXISTS fleet_lite_vfs_files RENAME TO fleet_lite_vfs_files_retired",)


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
    assert any("DELETE FROM fleet_lite_replay_snapshots WHERE created_at < NOW()" in sql for sql in statements)
    assert statements[-1] == "COMMIT"


def test_custom_schema_is_created_by_migration_pool_and_runtime_uses_runtime_pool():
    runtime_pool = FakePool([({"turns": []},)])
    migration_pool = FakePool()
    store = Store(
        runtime_pool,  # ty: ignore[invalid-argument-type]
        "postgresql://runtime",
        migration_pool=migration_pool,  # ty: ignore[invalid-argument-type]
        schema="fleet_rlm_lite",
    )

    store.initialize()
    store.history("s1")

    migration_sql = [sql for sql, _ in migration_pool.conn.statements]
    assert 'CREATE SCHEMA IF NOT EXISTS "fleet_rlm_lite"' in migration_sql
    assert 'SET LOCAL search_path TO "fleet_rlm_lite"' in migration_sql
    assert any("fleet_lite_sessions" in sql for sql in migration_sql)
    assert runtime_pool.borrowed == 1
    assert migration_pool.borrowed == 1


def test_store_open_scopes_runtime_and_migration_pools_to_the_configured_schema(monkeypatch):
    import fleet_rlm.store.db as store_db

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
    from fleet_rlm.store.db import SCHEMA_VERSION

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


def test_committing_a_turn_is_one_transaction_with_the_completion():
    store, pool = make_store()
    store.commit_turn(
        "s1",
        turn_record_json='{"turn_id":"turn-1"}',
        memory_note="note",
        completion=("t1", {"event": "turn_complete"}),
    )
    sql = [statement for statement, _ in pool.conn.statements]
    assert pool.borrowed == 1
    assert sql[0] == "BEGIN" and sql[-1] == "COMMIT"
    assert sql[2].endswith("FOR UPDATE")
    assert not any("fleet_lite_vfs_files" in statement for statement in sql)
    assert any(statement.startswith("UPDATE fleet_lite_sessions SET turns") for statement in sql)
    append_sql, append_params = next(
        (statement, params)
        for statement, params in pool.conn.statements
        if statement.startswith("UPDATE fleet_lite_sessions SET turns")
    )
    assert "%s::text IS NULL" in append_sql
    assert "NOT EXISTS" in append_sql and append_params[-2:] == ("turn-1", "turn-1")
    assert sql[-2].startswith("UPDATE fleet_lite_turns SET status='completed'")


def test_selected_replay_snapshot_commits_atomically_with_turn_completion():
    content = b"immutable input"
    digest = hashlib.sha256(content).hexdigest()
    snapshot = (digest, content)
    store, pool = make_store([], [], [], [], [(digest,)], [])
    store.commit_turn(
        "s1",
        turn_record_json='{"turn_id":"t1","replay_snapshot_selected":true}',
        completion=("t1", {"event": "turn_complete"}),
        replay_snapshot=snapshot,
    )
    sql = [statement for statement, _ in pool.conn.statements]
    assert sql[0] == "BEGIN" and sql[-1] == "COMMIT"
    assert any(statement.startswith("INSERT INTO fleet_lite_replay_snapshots") for statement in sql)
    assert any(statement.startswith("UPDATE fleet_lite_turns SET status='completed'") for statement in sql)
    assert sql.index(next(s for s in sql if s.startswith("INSERT INTO fleet_lite_replay_snapshots"))) < sql.index(
        next(s for s in sql if s.startswith("UPDATE fleet_lite_turns SET status='completed'"))
    )


def _harness(store: Any, files: list[str]) -> VolumePostgresHarness:
    sandbox = SimpleNamespace(session_id="s1", list_workspace_files=lambda: list(files))
    return VolumePostgresHarness(store, sandbox)  # ty: ignore[invalid-argument-type]


def test_harness_rejects_an_invalid_workspace_listing_before_any_sql_runs():
    store, pool = make_store()
    with pytest.raises(ValueError, match="invalid relative path"):
        _harness(store, ["../escape"]).commit_turn("s1", {"turn_id": "t1"})
    assert pool.borrowed == 0


def test_harness_reports_artifacts_and_records_the_completion_without_reading_files():
    calls = []
    store = SimpleNamespace(commit_turn=lambda *args, **kwargs: calls.append((args, kwargs)))
    artifacts = _harness(store, ["files/in.txt", "artifacts/out.txt"]).commit_turn(
        "s1", {"turn_id": "t1", "answer": "a", "route": "flex", "trace_id": "tr-1"}
    )
    assert artifacts == ["artifacts/out.txt"]
    (session_id,), kwargs = calls[0]
    assert session_id == "s1"
    turn_id, result = kwargs["completion"]
    assert turn_id == "t1" and result["trace_id"] == "tr-1" and result["artifacts"] == ["artifacts/out.txt"]
    assert json.loads(kwargs["turn_record_json"])["route"] == "flex"


def test_claim_turn_refuses_a_key_reused_for_another_task():
    prior = {"turn_id": "t0", "task_sha256": "other", "status": "running", "result": {}}
    store = SimpleNamespace(claim_turn=lambda *_args: prior)
    with pytest.raises(IdempotencyConflictError, match="another task"):
        _harness(store, []).claim_turn("t1", "key", "task")


def test_a_dropped_idle_connection_is_retried_once_after_a_pause(monkeypatch):
    import psycopg

    import fleet_rlm.store.db as store_db

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


def test_lakebase_connection_class_requests_a_fresh_oauth_token_per_connection(monkeypatch):
    import psycopg

    credentials = iter(("token-one", "token-two"))
    calls = []
    client = SimpleNamespace(
        postgres=SimpleNamespace(
            generate_database_credential=lambda *, endpoint: SimpleNamespace(token=next(credentials), endpoint=endpoint)
        )
    )

    def connect(connection_class, conninfo="", **kwargs):
        calls.append((connection_class, conninfo, kwargs))
        return kwargs

    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(connect))
    connection_class = lakebase_connection_class(client, "projects/p/branches/b/endpoints/e")
    first = connection_class.connect("host=lakebase", user="fleet_app")
    second = connection_class.connect("host=lakebase", user="fleet_app")
    assert first["password"] == "token-one" and second["password"] == "token-two"
    assert len(calls) == 2
    assert all(call[0] is connection_class and call[1] == "host=lakebase" for call in calls)


def test_commit_turn_requires_a_turn_id_so_a_connection_retry_cannot_duplicate_the_record():
    store, pool = make_store()
    with pytest.raises(ValueError, match="turn_id"):
        store.commit_turn("s1", turn_record_json='{"answer":"42"}')
    assert pool.borrowed == 0


def test_harness_rejects_a_turn_record_without_a_turn_id_before_any_sql_runs():
    calls: list = []
    store = SimpleNamespace(commit_turn=lambda *args, **kwargs: calls.append((args, kwargs)))
    with pytest.raises(ValueError, match="turn_id"):
        _harness(store, []).commit_turn("s1", {"answer": "42"})
    assert calls == []
