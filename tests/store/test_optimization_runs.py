from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from test_store import make_store

from fleet_rlm.store import _OPTIMIZATION_LOCK_SCOPE


def run_row(**changes: Any) -> dict[str, Any]:
    row = {
        "session_id": "s1",
        "run_id": "run-1",
        "request_id": "request-1",
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

    record = store.create_optimization_run(
        "s1", "run-1", "request-1", {"max_metric_calls": 20}, "worker-1", managed=True
    )

    assert record["run_id"] == "run-1" and record["session_id"] == "s1"
    assert record["status"] == "running" and record["stage"] == "search"
    assert record["candidate_version"] == "candidate-1"
    assert record["created_at"] == "2026-10-26T00:00:00+00:00"
    sql, params = pool.conn.statements[1]
    assert "ON CONFLICT (session_id,request_id) DO NOTHING RETURNING *" in sql
    assert params == ("s1", "run-1", "request-1", '{"max_metric_calls":20}', "worker-1", True)


def test_create_optimization_run_is_idempotent_for_the_same_request_and_details():
    existing = run_row()
    store, pool = make_store([], [existing])

    record = store.create_optimization_run(
        "s1", "new-generated-id", "request-1", {"max_metric_calls": 20}, "worker-1", managed=True
    )

    assert record["run_id"] == "run-1"
    assert pool.borrowed == 1
    assert len(pool.conn.statements) == 4
    assert "FOR UPDATE" in pool.conn.statements[2][0]


def test_create_optimization_run_rejects_reused_request_with_different_options():
    store, pool = make_store([], [run_row()])

    with pytest.raises(ValueError, match="different run details"):
        store.create_optimization_run("s1", "run-2", "request-1", {"max_metric_calls": 21}, "worker-1", managed=True)
    assert pool.borrowed == 1


def test_create_optimization_run_maps_global_active_unique_violation_to_busy():
    store, pool = make_store(error=psycopg.errors.UniqueViolation("active run exists"))

    with pytest.raises(ValueError, match="already active"):
        store.create_optimization_run("s1", "run-2", "request-2", {}, "worker-2")
    assert pool.conn.statements[1][0].startswith("INSERT INTO fleet_lite_optimization_runs")


def test_get_and_list_optimization_runs_are_session_scoped_and_canonical():
    completed = run_row(status="completed", metadata={"candidate_version": "candidate-1", "status": "spoofed"})
    store, pool = make_store([completed], [completed], [completed])

    one = store.get_optimization_run("s1", "run-1")
    assert one is not None and one["status"] == "completed"  # Ledger columns override metadata.
    assert store.list_optimization_runs("s1")[0]["candidate_version"] == "candidate-1"
    assert store.list_optimization_runs()[0]["session_id"] == "s1"
    assert pool.borrowed == 3
    assert "WHERE session_id=%s" in pool.conn.statements[1][0]
    assert "ORDER BY updated_at DESC" in pool.conn.statements[2][0]


def test_update_optimization_run_fences_owner_and_expected_status():
    store, pool = make_store([run_row(stage="validation", metadata={"candidate_version": "candidate-1", "score": 0.8})])

    record = store.update_optimization_run(
        "s1", "run-1", {"stage": "validation", "score": 0.8}, owner_id="worker-1", expected_statuses=("running",)
    )

    assert record is not None and record["stage"] == "validation" and record["score"] == 0.8
    statement, params = pool.conn.statements[0]
    assert "owner_id=%s" in statement and "status=ANY(%s::text[])" in statement
    assert params[7:9] == ("s1", "run-1") and params[-2:] == (["running"], ["running"])


def test_completed_update_atomically_yields_to_a_previously_accepted_stop_request():
    cancelled = run_row(
        status="cancelled",
        stage="cancelled",
        stop_requested=True,
        metadata={"candidate_version": None, "finished_at": "2026-10-26T12:00:00+00:00"},
    )
    store, pool = make_store([cancelled])

    record = store.update_optimization_run(
        "s1",
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


@pytest.mark.parametrize("key", ["session_id", "run_id", "request_id", "options", "owner_id", "managed"])
def test_update_optimization_run_rejects_identity_changes_before_sql(key: str):
    store, pool = make_store()

    with pytest.raises(ValueError, match="identity fields"):
        store.update_optimization_run("s1", "run-1", {key: "changed"})
    assert pool.borrowed == 0


def test_update_optimization_run_returns_none_when_a_fence_does_not_match():
    store, _pool = make_store([])
    assert store.update_optimization_run("s1", "run-1", {"stage": "done"}, owner_id="stale-worker") is None


def test_update_optimization_run_maps_active_status_collision_to_busy():
    store, _pool = make_store(error=psycopg.errors.UniqueViolation("active run exists"))

    with pytest.raises(ValueError, match="Another optimization run is active"):
        store.update_optimization_run("s1", "run-1", {"status": "running"})


def test_request_optimization_stop_is_idempotent_for_running_work():
    store, pool = make_store([run_row(stop_requested=True)])

    record = store.request_optimization_stop("s1", "run-1")

    assert record["status"] == "running" and record["stop_requested"] is True
    assert "status='running' RETURNING *" in pool.conn.statements[0][0]


def test_request_optimization_stop_refuses_a_terminal_run():
    store, _pool = make_store([], [run_row(status="completed")])

    with pytest.raises(ValueError, match="only a running"):
        store.request_optimization_stop("s1", "run-1")


def test_claim_optimization_review_claims_completed_candidate_and_recovers_approving_owner():
    store, pool = make_store([run_row(status="completed")], [run_row(status="approving", owner_id="reviewer-1")])
    claimed = store.claim_optimization_review("s1", "run-1", "reviewer-1")
    assert claimed["status"] == "approving" and claimed["owner_id"] == "reviewer-1"
    assert "status IN ('completed','approving') RETURNING *" in pool.conn.statements[2][0]

    store, _pool = make_store(
        [run_row(status="approving", owner_id="dead-reviewer")],
        [run_row(status="approving", owner_id="new-reviewer")],
    )
    recovered = store.claim_optimization_review("s1", "run-1", "new-reviewer")
    assert recovered["status"] == "approving" and recovered["owner_id"] == "new-reviewer"


def test_claim_optimization_review_maps_another_active_run_to_busy():
    store, _pool = make_store(error=psycopg.errors.UniqueViolation("active run exists"))

    with pytest.raises(ValueError, match="Another optimization run is active"):
        store.claim_optimization_review("s1", "run-1", "reviewer-1")


def test_reject_optimization_run_keeps_candidate_metadata_and_records_decision():
    store, pool = make_store(
        [run_row(status="rejected", metadata={"candidate_version": "candidate-1", "decision": "rejected"})]
    )

    rejected = store.reject_optimization_run("s1", "run-1")

    assert rejected["status"] == "rejected" and rejected["candidate_version"] == "candidate-1"
    assert "metadata=metadata || CASE WHEN status='rejected'" in pool.conn.statements[0][0]


def test_optimization_lease_uses_a_dedicated_connection_and_distinct_lock_scope(monkeypatch):
    connections = []

    class LeaseConnection:
        def __init__(self, locked: bool) -> None:
            self.locked = locked
            self.statements = []
            self.closed = False

        def execute(self, statement: str, params: tuple[Any, ...]):
            self.statements.append((statement, params))
            return type("Result", (), {"fetchone": lambda _self: (self.locked,)})()

        def close(self) -> None:
            self.closed = True

    def connect(*_args, **_kwargs):
        connection = LeaseConnection(not connections)
        connections.append(connection)
        return connection

    monkeypatch.setattr(
        psycopg.Connection, "connect", classmethod(lambda _cls, *args, **kwargs: connect(*args, **kwargs))
    )
    store, pool = make_store()

    held = store.acquire_optimization_lease()
    assert held is connections[0]
    assert held.statements[0][1] == (_OPTIMIZATION_LOCK_SCOPE,)
    assert pool.borrowed == 0

    with pytest.raises(RuntimeError, match="already owned"):
        store.acquire_optimization_lease()
    assert connections[1].closed


def test_owner_liveness_probe_uses_independent_connection_and_releases_probe_locks(monkeypatch):
    held = set()
    connections = []

    class Connection:
        def __init__(self):
            self.scopes = set()
            self.closed = False

        def execute(self, _sql, params):
            scope = params[0]
            available = scope not in held
            if available:
                held.add(scope)
                self.scopes.add(scope)
            return type("Result", (), {"fetchone": lambda _self: (available,)})()

        def close(self):
            held.difference_update(self.scopes)
            self.closed = True

    def connect(*_args, **_kwargs):
        connection = Connection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(lambda _cls, *args, **kw: connect(*args, **kw)))
    store, pool = make_store()
    owner = store.acquire_optimization_owner_lease("api-1")
    worker = store.acquire_optimization_lease()
    assert store.optimization_owner_alive("api-1") is True
    assert connections[-1].closed
    assert store.optimization_owner_alive("api-2") is False
    assert connections[-1].closed
    assert held == {"fleet:optimization-owner:api-1", _OPTIMIZATION_LOCK_SCOPE}
    owner.close()
    assert store.optimization_owner_alive("api-1") is False
    worker.close()
    assert not held and pool.borrowed == 0


def test_owner_probe_does_not_treat_database_errors_as_owner_death(monkeypatch):
    store, _pool = make_store()

    def fail(*_args, **_kwargs):
        raise psycopg.OperationalError("database unavailable")

    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(fail))
    with pytest.raises(psycopg.OperationalError):
        store.optimization_owner_alive("api-1")
