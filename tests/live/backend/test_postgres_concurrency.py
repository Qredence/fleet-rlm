"""Opt-in live proof: PostgreSQL Run-claim concurrency and outbox competition.

Gate: FLEET_LIVE=1 plus a disposable ``FLEET_DATABASE_URL`` targeting
PostgreSQL. The module never implicitly migrates a pre-populated shared
database: an empty target is migrated to the canonical Alembic head and
dropped afterwards; an existing target must already be at head.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import load_dotenv
from sqlalchemy import select, text

from fleet_rlm.persistence.database import (
    check_database_compatibility,
    create_async_engine_from_url,
    create_session_factory,
)

pytestmark = [pytest.mark.db]

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_repo_env() -> None:
    """Load repo ``.env`` into the process without overriding exported values."""
    load_dotenv(_REPO_ROOT / ".env", override=False)


def _skip_unless_live_postgres() -> str:
    if os.environ.get("FLEET_LIVE", "").strip() not in {"1", "true", "yes"}:
        pytest.skip("Set FLEET_LIVE=1 for live PostgreSQL concurrency tests")
    url = os.environ.get("FLEET_DATABASE_URL") or ""
    if not url:
        pytest.skip("FLEET_DATABASE_URL not configured")
    if not (url.startswith("postgres://") or url.startswith("postgresql")):
        pytest.skip("FLEET_DATABASE_URL is not a PostgreSQL URL")
    return url


def _prepare_database(url: str) -> bool:
    """Migrate an empty disposable target to head; verify any existing target.

    Returns whether this module created the schema (and therefore owns its
    teardown). A pre-populated database that is not at head fails closed
    instead of being migrated implicitly.
    """
    from sqlalchemy import inspect as sa_inspect

    async def probe() -> bool:
        engine = create_async_engine_from_url(url)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda sync_connection: sa_inspect(sync_connection).has_table("alembic_version")
                )
        finally:
            await engine.dispose()

    created_schema = not asyncio.run(probe())
    if created_schema:
        from tests.live.backend._database import upgrade_to_head

        upgrade_to_head(url)
    asyncio.run(check_database_compatibility(url))
    return created_schema


@pytest.fixture(scope="module")
def database_url() -> str:
    _load_repo_env()
    url = _skip_unless_live_postgres()
    created_schema = _prepare_database(url)
    yield url
    if created_schema:
        from fleet_rlm.persistence.models import Base

        async def drop_schema() -> None:
            engine = create_async_engine_from_url(url)
            try:
                async with engine.begin() as connection:
                    await connection.run_sync(Base.metadata.drop_all)
                    await connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
            finally:
                await engine.dispose()

        asyncio.run(drop_schema())


async def _seed_session(factory) -> tuple[object, object]:
    from fleet_rlm.persistence.models import SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.sessions.models import TurnAccess

    access = TurnAccess(uuid4(), uuid4())
    session_id = uuid4()
    # Parents flush before the child: the unit of work only orders mapper-level
    # dependencies, and PostgreSQL enforces column FKs immediately.
    async with factory() as db, db.begin():
        db.add_all(
            (
                UserRow(id=access.user_id),
                WorkspaceRow(id=access.workspace_id),
            )
        )
        await db.flush()
        db.add(
            SessionRow(
                id=session_id,
                user_id=access.user_id,
                workspace_id=access.workspace_id,
                title="live concurrency",
            )
        )
    return access, session_id


@pytest.mark.asyncio
async def test_many_simultaneous_claims_one_session(database_url: str) -> None:
    """Exactly one of N simultaneous claims wins; losers are typed refusals."""
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim, RunInProgressError
    from fleet_rlm.persistence.models import RunRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.models import TurnInput

    engine = create_async_engine_from_url(database_url)
    try:
        factory = create_session_factory(engine)
        access, session_id = await _seed_session(factory)
        store = SqlAlchemyRunStateStore(factory, stale_after_seconds=30)
        request = RunClaim(access, session_id, TurnInput("contended"), "shared", uuid4())

        results = await asyncio.gather(*(store.begin(request) for _ in range(16)), return_exceptions=True)
        winners = [result for result in results if isinstance(result, ClaimedRun)]
        losers = [result for result in results if isinstance(result, BaseException)]
        assert len(winners) == 1
        assert len(losers) == 15
        for loser in losers:
            # Expected contention maps to the domain refusal, never to
            # infrastructure unavailability.
            assert isinstance(loser, RunInProgressError)

        async with factory() as db:
            runs = (await db.scalars(select(RunRow).where(RunRow.session_id == session_id))).all()
        assert len(runs) == 1
        assert (runs[0].id, runs[0].status) == (winners[0].run_id, "running")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_parallel_sessions_claim_independently(database_url: str) -> None:
    """Concurrent claims across Sessions all win without cross-Session fencing."""
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunClaim
    from fleet_rlm.persistence.models import RunRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.models import TurnInput

    engine = create_async_engine_from_url(database_url)
    try:
        factory = create_session_factory(engine)
        store = SqlAlchemyRunStateStore(factory, stale_after_seconds=30)
        seeded = [await _seed_session(factory) for _ in range(8)]

        async def claim(index: int):
            access, session_id = seeded[index]
            return await store.begin(RunClaim(access, session_id, TurnInput(f"parallel-{index}"), "key", uuid4()))

        results = await asyncio.gather(*(claim(index) for index in range(8)))
        assert all(isinstance(result, ClaimedRun) for result in results)
        assert len({result.run_id for result in results}) == 8

        async with factory() as db:
            runs = (await db.scalars(select(RunRow).where(RunRow.session_id.in_([sid for _, sid in seeded])))).all()
        assert len(runs) == 8
        assert {run.session_id for run in runs} == {session_id for _, session_id in seeded}
        assert all(run.status == "running" for run in runs)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_claim_recovery_recovers_once_under_competition(database_url: str) -> None:
    """Concurrent reconcilers fence one stale claim through the CAS owner swap."""
    from fleet_rlm.persistence.models import RunRow, SessionRow, UserRow, WorkspaceRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.models import TurnAccess

    engine = create_async_engine_from_url(database_url)
    try:
        factory = create_session_factory(engine)
        access, session_id = TurnAccess(uuid4(), uuid4()), uuid4()
        run_id = uuid4()
        stale_heartbeat = datetime.now(UTC) - timedelta(hours=1)
        async with factory() as db, db.begin():
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                )
            )
            await db.flush()
            db.add(
                SessionRow(
                    id=session_id,
                    user_id=access.user_id,
                    workspace_id=access.workspace_id,
                    title="stale claim",
                )
            )
            await db.flush()
            db.add(
                RunRow(
                    id=run_id,
                    session_id=session_id,
                    status="running",
                    idempotency_key="stale",
                    input_fingerprint="a" * 64,
                    base_checkpoint_version=0,
                    claim_owner="stale-owner",
                    claim_heartbeat_at=stale_heartbeat,
                )
            )

        store = SqlAlchemyRunStateStore(factory, stale_after_seconds=30)
        fence_calls: list[object] = []

        async def fence(claimed_session_id):
            fence_calls.append(claimed_session_id)

        await asyncio.gather(
            store.reconcile_settling(fence),
            store.reconcile_settling(fence),
            store.reconcile_settling(fence),
        )
        # Earlier module tests may legitimately leave their own live claims,
        # which reconciliation may also recover. The once-only property is
        # asserted for the seeded stale run: it is fenced exactly once across
        # all competing reconcilers, then settles terminally once.
        assert fence_calls.count(session_id) == 1

        async with factory() as db:
            run = await db.get(RunRow, run_id)
            assert run is not None
            assert (run.status, run.failure_code) == ("failed", "stale_claim")
            assert run.claim_owner is None
            assert run.finished_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_outbox_workers_claim_each_intent_once(database_url: str) -> None:
    """Concurrent outbox workers never double-claim an intent (CAS rowcount gate)."""
    from fleet_rlm.persistence.models import (
        MemoryPromotionIntentRow,
        RunRow,
    )
    from fleet_rlm.persistence.repositories.outbox import SqlAlchemyMemoryPromotionOutbox
    from fleet_rlm.sessions.models import TurnAccess

    engine = create_async_engine_from_url(database_url)
    try:
        factory = create_session_factory(engine)
        access, session_id = TurnAccess(uuid4(), uuid4()), uuid4()
        run_id = uuid4()
        async with factory() as db, db.begin():
            from fleet_rlm.persistence.models import SessionRow, UserRow, WorkspaceRow

            # Lineage seeds flush parent-first: the unit of work only orders
            # mapper-level dependencies, and PostgreSQL enforces column FKs
            # immediately.
            db.add_all(
                (
                    UserRow(id=access.user_id),
                    WorkspaceRow(id=access.workspace_id),
                )
            )
            await db.flush()
            db.add(
                SessionRow(
                    id=session_id,
                    user_id=access.user_id,
                    workspace_id=access.workspace_id,
                    title="outbox competition",
                )
            )
            await db.flush()
            db.add(
                RunRow(
                    id=run_id,
                    session_id=session_id,
                    status="completed",
                    idempotency_key="outbox",
                    input_fingerprint="b" * 64,
                    base_checkpoint_version=0,
                    commit_checkpoint_version=1,
                )
            )
            await db.flush()
            db.add_all(
                MemoryPromotionIntentRow(
                    run_id=run_id,
                    session_id=session_id,
                    workspace_id=access.workspace_id,
                    user_id=access.user_id,
                    candidate_ordinal=ordinal,
                    candidate_id=f"cand{ordinal:08x}"[:12].ljust(12, "0"),
                    category="General",
                    learning=f"live intent {ordinal}",
                    byte_size=len(f"live intent {ordinal}".encode()),
                    memory_id=f"mem{ordinal:05d}"[:8].ljust(8, "0"),
                    record_text=f"record {ordinal}",
                )
                for ordinal in range(12)
            )

        outbox = SqlAlchemyMemoryPromotionOutbox(factory)
        now = datetime.now(UTC)
        workers = tuple(outbox.claim_due(now=now, claim_owner=f"worker:{index}", limit=100) for index in range(4))
        batches = await asyncio.gather(*workers)
        claimed = [intent for batch in batches for intent in batch]
        claimed_ids = [intent.intent_id for intent in claimed]
        assert sorted(claimed_ids) == sorted(await _seeded_intent_ids(factory, run_id))
        # No intent was claimed twice and no worker error escaped.
        assert len(claimed_ids) == len(set(claimed_ids))

        delivered = await outbox.complete(tuple(claimed_ids), completion_reason="delivered")
        assert delivered == len(claimed_ids)
        async with factory() as db:
            rows = (await db.scalars(select(MemoryPromotionIntentRow))).all()
        assert all(row.status == "completed" for row in rows)
        assert all(row.claim_owner is None for row in rows)
    finally:
        await engine.dispose()


async def _seeded_intent_ids(factory, run_id) -> list:
    from fleet_rlm.persistence.models import MemoryPromotionIntentRow

    async with factory() as db:
        return list(
            (
                await db.scalars(select(MemoryPromotionIntentRow.id).where(MemoryPromotionIntentRow.run_id == run_id))
            ).all()
        )


@pytest.mark.asyncio
async def test_cancellation_racing_settlement_keeps_one_terminal_state(database_url: str) -> None:
    """request_cancel and commit race to exactly one terminal Run state."""
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, CommittedTurnReceipt, RunClaim
    from fleet_rlm.persistence.models import RunRow
    from fleet_rlm.persistence.repositories.turns import SqlAlchemyRunStateStore
    from fleet_rlm.sessions.committed_turn import CommittedTurn, TextPart, UsagePart
    from fleet_rlm.sessions.models import TurnInput

    engine = create_async_engine_from_url(database_url)
    try:
        factory = create_session_factory(engine)
        access, session_id = await _seed_session(factory)
        store = SqlAlchemyRunStateStore(factory, stale_after_seconds=30)
        winner = await store.begin(RunClaim(access, session_id, TurnInput("cancel race"), "key", uuid4()))
        assert isinstance(winner, ClaimedRun)
        committed = CommittedTurn(
            1,
            (UsagePart({"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}), TextPart("settled")),
        )

        cancel_outcome, commit_outcome = await asyncio.gather(
            store.request_cancel(access, winner.run_id),
            store.commit(winner, committed, ()),
        )
        # Cancellation is advisory: the settlement keeps its valid claim and
        # the Run lands in exactly one terminal state.
        assert cancel_outcome in {"requested", "already_terminal"}
        assert isinstance(commit_outcome, CommittedTurnReceipt)

        async with factory() as db:
            runs = (await db.scalars(select(RunRow).where(RunRow.session_id == session_id))).all()
        assert len(runs) == 1
        assert (runs[0].status, runs[0].claim_owner, runs[0].finished_at is not None) == ("completed", None, True)
        assert runs[0].commit_checkpoint_version == 1
    finally:
        await engine.dispose()
