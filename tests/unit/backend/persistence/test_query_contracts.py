"""Repository query counts, plans, and live-lane query-plan replay.

* ``test_query_plan_scenarios.py``: Execute query-plan workload setup locally without claiming PostgreSQL proof.
* ``test_persistence_query_contracts.py``: Phase 1 query counts and SQLite plans from executable repository calls.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event

from fleet_rlm.persistence.repositories.outbox import SqlAlchemyMemoryPromotionOutbox
from fleet_rlm.persistence.repositories.sessions import SqlAlchemySessionCatalog
from fleet_rlm.sessions.models import TurnInput
from fleet_rlm.sessions.run_state import RunClaim
from tests.support.memory_intents import _intents, _seed_with_intents
from tests.support.query_scenarios import repository_query_plan
from tests.support.sqlite_claims import postgres_claim_store

# --- from test_query_plan_scenarios.py --------------------------------
__all__ = ["postgres_claim_store"]
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def local_query_plan_policy(monkeypatch):
    monkeypatch.setenv("FLEET_TEST_DATABASE_EXCLUSIVE", "1")
    monkeypatch.setenv("FLEET_POSTGRES_QUERY_SAMPLES", "8")


@pytest.mark.parametrize("operation", ["sessions", "history", "replay", "recovery", "outbox"])
async def test_postgres_repository_query_plan(postgres_claim_store, record_testsuite_property, operation):
    await repository_query_plan(postgres_claim_store, record_testsuite_property, operation)


# --- from test_persistence_query_contracts.py -------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation, expected_statements",
    [("sessions", 2), ("history", 1), ("replay", 3), ("recovery", 1), ("outbox", 3)],
)
async def test_query_counts_and_sqlite_plans(tmp_path, operation, expected_statements):
    engine, factory, store, run, access = await _seed_with_intents(
        f"sqlite+aiosqlite:///{tmp_path / 'queries.db'}", intents=_intents()
    )
    statements = []

    def capture(_connection, _cursor, statement, parameters, _context, _executemany):
        statements.append((statement, parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        if operation == "sessions":
            page = await SqlAlchemySessionCatalog(factory).list(
                user_id=access.user_id,
                workspace_id=access.workspace_id,
                status="active",
                search=None,
                limit=20,
                offset=0,
            )
            assert page.total == 1
        elif operation == "history":
            async with factory() as db:
                await store._history(db, run.session_id)
        elif operation == "replay":
            replay = await store._reconcile_claim_conflict(
                RunClaim(access, run.session_id, TurnInput("hello"), "key", uuid4())
            )
            assert replay.run_id == run.run_id
        elif operation == "recovery":
            assert await store._load_recovery_candidates() == []
        else:
            claimed = await SqlAlchemyMemoryPromotionOutbox(factory).claim_due(
                now=datetime.now(UTC), claim_owner="query-test"
            )
            assert len(claimed) == 2
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
        assert len(statements) == expected_statements
        # Explain precisely the SELECTs executed above, retaining parameters
        # only in this private fixture. No SQL values enter runtime telemetry.
        async with engine.connect() as connection:
            for statement, parameters in statements:
                if statement.lstrip().upper().startswith("SELECT"):
                    plan = await connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters)
                    assert plan.all(), operation
    finally:
        if event.contains(engine.sync_engine, "before_cursor_execute", capture):
            event.remove(engine.sync_engine, "before_cursor_execute", capture)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("fence_fails", [False, True])
async def test_recovery_releases_database_connection_before_provider_fence(tmp_path, fence_fails):
    engine, _factory, store, run, _access = await _seed_with_intents(
        f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}", commit=False
    )
    checked_out = 0
    fenced = []

    def checkout(*_args):
        nonlocal checked_out
        checked_out += 1

    def checkin(*_args):
        nonlocal checked_out
        checked_out -= 1

    async def fence(session_id):
        assert checked_out == 0
        fenced.append(session_id)
        if fence_fails:
            raise RuntimeError("controlled provider fence failure")

    event.listen(engine.sync_engine, "checkout", checkout)
    event.listen(engine.sync_engine, "checkin", checkin)
    try:
        summary = await store.reconcile_settling(fence)
        assert fenced == [run.session_id]
        assert summary.fence_failures == int(fence_fails)
        assert summary.recovered == int(not fence_fails)
        assert checked_out == 0
    finally:
        event.remove(engine.sync_engine, "checkout", checkout)
        event.remove(engine.sync_engine, "checkin", checkin)
        await engine.dispose()
