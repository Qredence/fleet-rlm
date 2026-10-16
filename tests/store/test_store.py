"""The Store runs every SQL statement over one pool; the harness validates before anything is written."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.store import Store
from fleet_rlm.store.db import SCHEMA_STATEMENTS, VolumePostgresHarness


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeCursor:
        self.connection.statements.append((" ".join(sql.split()), params))
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


def test_every_call_borrows_one_pooled_connection():
    store, pool = make_store([("s1", dt.datetime(2026, 1, 1), 2)], [({"turns": [1]},)])
    store.list_sessions()
    store.runtime_settings("s1")
    assert pool.borrowed == 2


def test_memories_are_marked_stale_when_a_source_digest_changed():
    updated = dt.datetime(2026, 10, 15, tzinfo=dt.UTC)
    memory = {
        "memory_id": "m1",
        "content": "c",
        "kind": "observation",
        "sources": {"files/a": "old"},
        "reviewed": True,
        "updated_at": updated,
    }
    store, _ = make_store([memory], [("files/a", "new")])
    [row] = store.memories("s1", "", reviewed_only=True)
    assert row["stale"] is True and row["updated_at"] == updated.isoformat()


def test_mirroring_a_turn_is_one_transaction_with_the_completion():
    store, pool = make_store()
    store.mirror_files(
        "s1",
        [("files/a.txt", "abc", b"x")],
        turn_record_json="{}",
        memory_note="note",
        completion=("t1", {"event": "turn_complete"}),
    )
    sql = [statement for statement, _ in pool.conn.statements]
    assert pool.borrowed == 1
    assert sql[0] == "BEGIN" and sql[-1] == "COMMIT"
    assert sql[2].endswith("FOR UPDATE")
    assert any(statement.startswith("INSERT INTO fleet_lite_vfs_files") for statement in sql)
    assert any(statement.startswith("UPDATE fleet_lite_sessions SET turns") for statement in sql)
    assert sql[-2].startswith("UPDATE fleet_lite_turns SET status='completed'")


def _harness(store: Any, files: dict[str, bytes]) -> VolumePostgresHarness:
    sandbox = SimpleNamespace(
        session_id="s1",
        list_workspace_files=lambda: list(files),
        read_workspace_file=lambda path, **_limits: files[path],
    )
    return VolumePostgresHarness(store, sandbox)  # ty: ignore[invalid-argument-type]


def test_harness_rejects_a_bad_scan_before_any_sql_runs():
    store, pool = make_store()
    harness = _harness(store, {"../escape": b"x"})
    with pytest.raises(ValueError):
        harness.persist_workspace_files("s1")
    assert pool.borrowed == 0


def test_harness_reports_artifacts_and_records_the_completion():
    calls = []
    store = SimpleNamespace(mirror_files=lambda *args, **kwargs: calls.append((args, kwargs)))
    files = {"files/in.txt": b"in", "artifacts/out.txt": b"out"}
    artifacts = _harness(store, files).sync_volume_to_postgres(
        "s1", {"turn_id": "t1", "answer": "a", "route": "flex", "trace_id": "tr-1"}
    )
    assert artifacts == ["artifacts/out.txt"]
    (session_id, verified), kwargs = calls[0]
    assert session_id == "s1"
    assert [(path, digest) for path, digest, _ in verified] == [
        (path, hashlib.sha256(raw).hexdigest()) for path, raw in files.items()
    ]
    turn_id, result = kwargs["completion"]
    assert turn_id == "t1" and result["trace_id"] == "tr-1" and result["artifacts"] == ["artifacts/out.txt"]
    assert json.loads(kwargs["turn_record_json"])["route"] == "flex"


def test_claim_turn_refuses_a_key_reused_for_another_task():
    prior = {"turn_id": "t0", "task_sha256": "other", "status": "running", "result": {}}
    store = SimpleNamespace(claim_turn=lambda *_args: prior)
    with pytest.raises(ValueError, match="another task"):
        _harness(store, {}).claim_turn("t1", "key", "task")


def test_a_dropped_idle_connection_is_retried_once():
    import psycopg

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
    assert len(calls) == 2
