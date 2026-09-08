"""Opt-in PostgreSQL claim races; only uniquely owned fixture rows are removed.

Requires FLEET_LIVE=1 and an explicitly exported FLEET_DATABASE_URL at
the current Alembic head. Does not load dotenv, migrate, or select global work.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from fleet_rlm.chat.run_lifecycle import (
    ClaimedRun,
    CommittedRunReplay,
    RunClaim,
    RunIdempotencyMismatchError,
    RunInProgressError,
    RunStateError,
)
from fleet_rlm.persistence.database import (
    check_database_compatibility,
    create_async_engine_from_url,
    create_session_factory,
)
from fleet_rlm.persistence.models import MemoryPromotionIntentRow, RunRow, SessionRow, UserRow, WorkspaceRow
from fleet_rlm.persistence.repositories import SqlAlchemyRunStateStore, SqlAlchemySessionCatalog
from fleet_rlm.persistence.repositories.outbox import SqlAlchemyMemoryPromotionOutbox
from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
from fleet_rlm.sessions.models import TurnAccess, TurnInput

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def postgres_claim_store():
    if os.environ.get("FLEET_LIVE", "").strip() not in {"1", "true", "yes"}:
        pytest.skip("Set FLEET_LIVE=1 to authorize live PostgreSQL tests")
    url = os.environ.get("FLEET_DATABASE_URL", "")
    if not url.startswith(("postgres://", "postgresql")):
        pytest.skip("Export a PostgreSQL FLEET_DATABASE_URL")
    await check_database_compatibility(url)
    engine = create_async_engine_from_url(url)
    factory = create_session_factory(engine)
    access = TurnAccess(uuid4(), uuid4())
    session_id = None
    try:
        record = await SqlAlchemySessionCatalog(factory).create(
            user_id=access.user_id, workspace_id=access.workspace_id, title="claim-contention"
        )
        session_id = record.id
        store = SqlAlchemyRunStateStore(factory)
        yield store, factory, access, session_id
    finally:
        try:
            async with factory() as db, db.begin():
                if session_id is not None:
                    await db.execute(delete(SessionRow).where(SessionRow.id == session_id))
                await db.execute(delete(WorkspaceRow).where(WorkspaceRow.id == access.workspace_id))
                await db.execute(delete(UserRow).where(UserRow.id == access.user_id))
        finally:
            await engine.dispose()


@pytest.mark.parametrize("race", ["duplicate", "conflicting_input", "active_run"])
async def test_postgres_concurrent_claims_have_one_owner(postgres_claim_store, race):
    store, factory, access, session_id = postgres_claim_store
    # Keep the competing tasks inside one bounded lifetime before fixture cleanup.
    async with asyncio.timeout(30):
        requests = (
            RunClaim(access, session_id, TurnInput("first"), "first", uuid4()),
            RunClaim(
                access,
                session_id,
                TurnInput("second" if race == "conflicting_input" else "first"),
                "second" if race == "active_run" else "first",
                uuid4(),
            ),
        )
        ready = asyncio.Event()

        async def begin(request):
            await ready.wait()
            return await store.begin(request)

        tasks = [asyncio.create_task(begin(request)) for request in requests]
        ready.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        winners = [result for result in results if isinstance(result, ClaimedRun)]
        assert len(winners) == 1
        expected = RunIdempotencyMismatchError if race == "conflicting_input" else RunInProgressError
        assert sum(isinstance(result, expected) for result in results) == 1
        winner = winners[0]
        async with factory() as db:
            rows = (await db.scalars(select(RunRow).where(RunRow.session_id == session_id))).all()
            assert [(row.id, row.status) for row in rows] == [(winner.run_id, "running")]
        await store.commit(
            winner,
            CommittedTurn(1, (UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}), TextPart("ok"))),
            (),
        )
        winning_request = next(request for request in requests if request.proposed_run_id == winner.run_id)
        replay = await store.begin(winning_request)
        assert isinstance(replay, CommittedRunReplay)
        assert replay.run_id == winner.run_id


async def test_postgres_cancel_settlement_races_commit(postgres_claim_store):
    from fleet_rlm.chat.run_claim import BeginSettlement, ClaimFailure, CompleteSettlement

    store, factory, access, session_id = postgres_claim_store
    run = await store.begin(RunClaim(access, session_id, TurnInput("cancel race"), "cancel", uuid4()))
    assert isinstance(run, ClaimedRun)
    committed = CommittedTurn(
        1, (UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}), TextPart("ok"))
    )
    async with asyncio.timeout(30):
        results = await asyncio.gather(
            store.commit(run, committed, ()),
            store.transition_claim(run, BeginSettlement(ClaimFailure("cancelled", "cancelled", "Cancelled"))),
            return_exceptions=True,
        )
    # A completed commit or cancellation settlement wins atomically; neither
    # a raw database error nor a partially committed checkpoint is acceptable.
    assert all(not isinstance(result, BaseException) or isinstance(result, RunStateError) for result in results), [
        type(result).__name__ for result in results
    ]
    async with factory() as db:
        row = await db.get(RunRow, run.run_id)
        session = await db.get(SessionRow, session_id)
        assert row is not None and session is not None
        assert (row.status, session.checkpoint_version) in {("completed", 1), ("settling", 0)}
        cancelled = row.status == "settling"
    if cancelled:
        await store.transition_claim(run, CompleteSettlement())
        with pytest.raises(RunStateError):
            await store.commit(run, committed, ())


async def test_postgres_recovery_owner_cas_fences_stale_commit(postgres_claim_store):
    store, factory, access, session_id = postgres_claim_store
    run = await store.begin(RunClaim(access, session_id, TurnInput("recovery race"), "recovery", uuid4()))
    assert isinstance(run, ClaimedRun)
    async with factory() as db:
        pending = await db.get(RunRow, run.run_id)
        assert pending is not None
    # Target the owned row through the production CAS seam, never a global
    # startup recovery scan that could claim another operator's running Turn.
    async with asyncio.timeout(30):
        owners = await asyncio.gather(store._claim_recovery_owner(pending), store._claim_recovery_owner(pending))
    winning = [owner for owner in owners if owner is not None]
    assert len(winning) == 1
    assert not await store._complete_recovery(pending, "foreign-recovery-owner")
    assert await store._complete_recovery(pending, winning[0])
    assert not await store._complete_recovery(pending, winning[0])
    with pytest.raises(RunStateError):
        await store.commit(
            run,
            CommittedTurn(
                1, (UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}), TextPart("late"))
            ),
            (),
        )
    async with factory() as db:
        row = await db.get(RunRow, run.run_id)
        session = await db.get(SessionRow, session_id)
        assert row is not None and session is not None
        assert (row.status, row.claim_owner, session.checkpoint_version) == ("failed", None, 0)


async def test_postgres_outbox_claims_are_disjoint(postgres_claim_store):
    from fleet_rlm.workspace.memory import MemoryCandidate, build_memory_promotion_intents

    store, factory, access, session_id = postgres_claim_store
    async with factory() as db:
        if db.get_bind().dialect.name != "sqlite" and os.environ.get("FLEET_TEST_DATABASE_EXCLUSIVE") != "1":
            pytest.skip("Global outbox lane requires an exclusive test database: FLEET_TEST_DATABASE_EXCLUSIVE=1")
        assert await db.scalar(select(MemoryPromotionIntentRow.id).limit(1)) is None, "Test outbox must be empty"
    run = await store.begin(RunClaim(access, session_id, TurnInput("outbox race"), "outbox", uuid4()))
    assert isinstance(run, ClaimedRun)
    now = datetime.now(UTC)
    intents = build_memory_promotion_intents(
        run_id=run.run_id,
        candidates=tuple(
            MemoryCandidate(candidate_id=f"{index:012x}", category="General", learning=f"test {index}", byte_size=6)
            for index in range(3)
        ),
        allowed_categories=("General",),
        clock=lambda: now,
    )
    await store.commit(
        run,
        CommittedTurn(1, (UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}), TextPart("ok"))),
        (),
        memory_intents=intents,
    )
    outbox = SqlAlchemyMemoryPromotionOutbox(factory)
    # The commit is allowed to choose its own microsecond-resolution due time;
    # claim strictly after it so this remains a concurrency test, not a clock
    # precision test across PostgreSQL and SQLite.
    due_now = now + timedelta(seconds=1)
    async with asyncio.timeout(30):
        first, second = await asyncio.gather(
            outbox.claim_due(now=due_now, claim_owner="first"),
            outbox.claim_due(now=due_now, claim_owner="second"),
        )
    assert {item.intent_id for item in first}.isdisjoint(item.intent_id for item in second)
    assert len(first) + len(second) == 3
    assert all(item.run_id == run.run_id and item.attempts == 1 for item in (*first, *second))
    assert await outbox.claim_due(now=due_now, claim_owner="third") == ()
