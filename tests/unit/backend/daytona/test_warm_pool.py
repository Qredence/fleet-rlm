from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.warm_pool import (
    WarmPoolCampaign,
    WarmPoolError,
    WarmPoolOwnership,
    WarmPoolPlan,
    reconcile_warm_pool,
)


class Client:
    def __init__(self, pools=()):
        self.pools = list(pools)
        self.calls: list[tuple[object, ...]] = []

    async def list(self):
        self.calls.append(("list",))
        return self.pools

    async def create(self, snapshot, pool, target=None):
        self.calls.append(("create", snapshot, pool, target))
        created = SimpleNamespace(id="created", snapshot=snapshot, target=target, pool=pool, current_size=0)
        self.pools.append(created)
        return created

    async def update(self, pool_id, pool):
        self.calls.append(("update", pool_id, pool))
        return SimpleNamespace(id=pool_id, pool=pool, current_size=0)

    async def delete(self, pool_id):
        self.calls.append(("delete", pool_id))
        self.pools = [pool for pool in self.pools if getattr(pool, "id", None) != pool_id]


class OwnershipStore:
    def __init__(self, ownership=None):
        self.ownership = ownership

    async def find(self, **_kwargs):
        return self.ownership

    async def save(self, ownership):
        self.ownership = ownership
        return ownership


class FailingOwnershipStore(OwnershipStore):
    async def save(self, **_kwargs):
        raise RuntimeError("storage unavailable")


def _settings(**overrides):
    return Settings(daytona_child_snapshot="fleet-child-v1", **overrides)


def _campaign() -> WarmPoolCampaign:
    return WarmPoolCampaign("test-campaign", 10, 1800, 1, 1)


@pytest.mark.asyncio
async def test_disabled_policy_never_contacts_provider():
    client = Client()
    result = await reconcile_warm_pool(client, WarmPoolPlan.from_settings(_settings()), apply=True)
    assert result.action == "disabled"
    assert client.calls == []


@pytest.mark.asyncio
async def test_plan_then_reconcile_creates_clean_child_pool():
    settings = _settings(daytona_warm_pool_enabled=True, daytona_warm_pool_size=1, daytona_warm_pool_region="us")
    plan = WarmPoolPlan.from_settings(settings)
    client = Client()
    assert (await reconcile_warm_pool(client, plan)).action == "create"
    store = OwnershipStore()
    assert (
        await reconcile_warm_pool(
            client, plan, apply=True, campaign=_campaign(), ownership_store=store, candidate_sha="a" * 64
        )
    ).action == "created"
    assert client.calls[-1] == ("create", "fleet-child-v1", 1, "us")


@pytest.mark.asyncio
async def test_failed_ownership_save_deletes_and_confirms_new_pool_cleanup():
    settings = _settings(daytona_warm_pool_enabled=True, daytona_warm_pool_size=1)
    plan = WarmPoolPlan.from_settings(settings)
    client = Client()
    with pytest.raises(WarmPoolError, match="cleaned up"):
        await reconcile_warm_pool(
            client,
            plan,
            apply=True,
            campaign=_campaign(),
            ownership_store=FailingOwnershipStore(),
            candidate_sha="a" * 64,
        )
    assert client.calls[-2:] == [("delete", "created"), ("list",)]
    assert client.pools == []


@pytest.mark.asyncio
async def test_failed_ownership_save_fails_safe_when_cleanup_is_unconfirmed():
    settings = _settings(daytona_warm_pool_enabled=True, daytona_warm_pool_size=1)
    plan = WarmPoolPlan.from_settings(settings)
    client = Client()

    async def delete_without_removing(_pool_id):
        client.calls.append(("delete", _pool_id))

    client.delete = delete_without_removing
    with pytest.raises(WarmPoolError, match="could not be confirmed"):
        await reconcile_warm_pool(
            client,
            plan,
            apply=True,
            campaign=_campaign(),
            ownership_store=FailingOwnershipStore(),
            candidate_sha="a" * 64,
        )


@pytest.mark.asyncio
async def test_matching_pool_is_updated_idempotently():
    settings = _settings(daytona_warm_pool_enabled=True, daytona_warm_pool_size=1)
    plan = WarmPoolPlan.from_settings(settings)
    pool = SimpleNamespace(id="pool-1", snapshot="fleet-child-v1", target=None, pool=0, current_size=0)
    client = Client([pool])
    store = OwnershipStore(
        WarmPoolOwnership("pool-1", "test-campaign", plan.snapshot, plan.target, plan.manifest_sha256, "a" * 64)
    )
    assert (await reconcile_warm_pool(client, plan, ownership_store=store, candidate_sha="a" * 64)).action == "update"
    updated = await reconcile_warm_pool(
        client, plan, apply=True, campaign=_campaign(), ownership_store=store, candidate_sha="a" * 64
    )
    assert updated.action == "updated"
    pool.pool = 1
    unchanged = await reconcile_warm_pool(
        client, plan, apply=True, campaign=_campaign(), ownership_store=store, candidate_sha="a" * 64
    )
    assert unchanged.action == "unchanged"


@pytest.mark.asyncio
async def test_duplicate_matching_pools_fail_closed():
    settings = _settings(daytona_warm_pool_enabled=True, daytona_warm_pool_size=1)
    plan = WarmPoolPlan.from_settings(settings)
    pools = [SimpleNamespace(id=str(index), snapshot="fleet-child-v1", target=None, pool=1) for index in range(2)]
    with pytest.raises(WarmPoolError, match="multiple"):
        await reconcile_warm_pool(Client(pools), plan)


@pytest.mark.asyncio
async def test_existing_pool_requires_explicit_ownership_or_adoption():
    plan = WarmPoolPlan.from_settings(_settings(daytona_warm_pool_enabled=True, daytona_warm_pool_size=1))
    pool = SimpleNamespace(id="pool-1", snapshot="fleet-child-v1", target=None, pool=0, current_size=0)
    with pytest.raises(WarmPoolError, match="not Fleet-owned"):
        await reconcile_warm_pool(Client([pool]), plan, apply=True, campaign=_campaign())
    store = OwnershipStore()
    result = await reconcile_warm_pool(
        Client([pool]),
        plan,
        apply=True,
        campaign=_campaign(),
        ownership_store=store,
        candidate_sha="a" * 64,
        adopt=True,
    )
    assert result.action == "updated"
