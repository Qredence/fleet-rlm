from __future__ import annotations

import pytest

from fleet_rlm.daytona.warm_pool import WarmPoolOwnership
from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory, create_tables
from fleet_rlm.persistence.models import WarmPoolOwnershipRow
from fleet_rlm.persistence.repositories.warm_pool import SqlAlchemyWarmPoolOwnershipStore


@pytest.mark.asyncio
async def test_warm_pool_ownership_lookup_is_exact_provider_identity() -> None:
    """A stale same-definition record must not authorize another provider pool."""
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        sessions = create_session_factory(engine)
        store = SqlAlchemyWarmPoolOwnershipStore(sessions)
        candidate = "a" * 64
        manifest = "b" * 64
        await store.save(
            WarmPoolOwnership(
                "pool-live",
                "canary",
                "fleet-child-v1",
                "us",
                manifest,
                candidate,
            )
        )
        async with sessions.begin() as session:
            session.add(
                WarmPoolOwnershipRow(
                    pool_id="pool-stale",
                    campaign="old-canary",
                    snapshot="fleet-child-v1",
                    target="us",
                    manifest_sha256=manifest,
                    candidate_sha=candidate,
                    generation=7,
                    status="retired",
                )
            )

        live = await store.find(pool_id="pool-live")
        stale = await store.find(pool_id="pool-stale")
        assert live is not None
        assert live.pool_id == "pool-live"
        assert live.status == "owned"
        assert live.reconciliation_generation == 1
        assert stale is not None
        assert stale.status == "retired"
        assert stale.reconciliation_generation == 7
        assert await store.find(pool_id="pool-unknown") is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_warm_pool_ownership_save_returns_monotonic_generation_and_status() -> None:
    engine = create_async_engine_from_url("sqlite+aiosqlite:///:memory:")
    try:
        await create_tables(engine)
        store = SqlAlchemyWarmPoolOwnershipStore(create_session_factory(engine))
        initial = WarmPoolOwnership("pool-1", "campaign", "snapshot", "us", "b" * 64, "a" * 64)
        assert (await store.save(initial)).reconciliation_generation == 1

        retired = WarmPoolOwnership("pool-1", "campaign", "snapshot", "us", "b" * 64, "a" * 64, status="retired")
        saved = await store.save(retired)

        assert saved.reconciliation_generation == 2
        assert saved.status == "retired"
        found = await store.find(pool_id="pool-1")
        assert found is not None
        assert found.reconciliation_generation == 2
        assert found.status == "retired"
    finally:
        await engine.dispose()
