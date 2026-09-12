"""Shared scenario setup; not a collected test module."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event

from fleet_rlm.chat.run_lifecycle import RunClaim
from fleet_rlm.persistence.repositories import SqlAlchemySessionCatalog
from fleet_rlm.persistence.repositories.outbox import SqlAlchemyMemoryPromotionOutbox
from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
from fleet_rlm.sessions.models import TurnInput
from scripts.benchmarks.certify_postgres import project_query_plan


async def repository_query_plan(postgres_claim_store, record_testsuite_property, operation):
    if os.environ.get("FLEET_TEST_DATABASE_EXCLUSIVE") != "1":
        pytest.skip("Query-plan lane requires an explicitly exclusive test database")
    samples = int(os.environ.get("FLEET_POSTGRES_QUERY_SAMPLES", "64"))
    assert 1 <= samples <= 1000
    store, factory, access, session_id = postgres_claim_store
    catalog = SqlAlchemySessionCatalog(factory)
    committed = CommittedTurn(
        1, (UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}), TextPart("fixture"))
    )
    request = None
    for index in range(samples):
        target = session_id
        if operation == "sessions":
            target = (
                await catalog.create(
                    user_id=access.user_id, workspace_id=access.workspace_id, title="query-plan-fixture"
                )
            ).id
        request = RunClaim(access, target, TurnInput("fixture"), f"query-{index}", uuid4())
        run = await store.begin(request)
        await store.commit(run, committed, ())
    engine = factory.kw["bind"]
    statements = []

    def capture(_connection, _cursor, statement, parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append((statement, parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        if operation == "sessions":
            await catalog.list(
                user_id=access.user_id,
                workspace_id=access.workspace_id,
                status="active",
                search=None,
                limit=20,
                offset=0,
            )
        elif operation == "history":
            async with factory() as db:
                await store._history(db, session_id)
        elif operation == "replay":
            await store._reconcile_claim_conflict(request)
        elif operation == "recovery":
            await store._load_recovery_candidates()
        else:
            await SqlAlchemyMemoryPromotionOutbox(factory).claim_due(
                now=datetime.now(UTC), claim_owner="query-plan-fixture"
            )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    assert statements
    plans = []
    async with engine.connect() as connection:
        for statement, parameters in statements:
            if connection.dialect.name == "sqlite":
                # Shared local scenario coverage cannot generate PostgreSQL
                # certification: the live fixture rejects SQLite targets.
                result = await connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters)
                assert result.all()
                plan = [{"Plan": {"Node Type": "Other"}}]
            else:
                result = await connection.exec_driver_sql("EXPLAIN (FORMAT JSON) " + statement, parameters)
                plan = result.scalar_one()
                if isinstance(plan, str):
                    plan = json.loads(plan)
            plans.append(
                {
                    "statement_sha256": hashlib.sha256(statement.encode()).hexdigest(),
                    "plan": project_query_plan(plan[0]["Plan"]),
                }
            )
    record_testsuite_property(
        f"fleet.postgres.query_plan.{operation}",
        json.dumps({"fixture_samples": samples, "basis": "synthetic", "plans": plans}, sort_keys=True),
    )
