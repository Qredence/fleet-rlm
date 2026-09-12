from __future__ import annotations

from types import SimpleNamespace

import pytest
from daytona_api_client_async import CreateWarmPool

from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.warm_pool import (
    WarmPoolCampaign,
    WarmPoolError,
    WarmPoolOwnership,
    WarmPoolPlan,
    reconcile_warm_pool,
    validate_semantic_child_warm_pool_request,
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


class ConflictClient(Client):
    def __init__(self, pool):
        super().__init__()
        self._pool = pool

    async def create(self, snapshot, pool, target=None):
        self.calls.append(("create", snapshot, pool, target))
        self.pools.append(self._pool)
        error = RuntimeError("provider conflict")
        error.status_code = 409  # type: ignore[attr-defined]
        raise error


class MismatchedCreateClient(Client):
    def __init__(self, field, value):
        super().__init__()
        self.field = field
        self.value = value

    async def create(self, snapshot, pool, target=None):
        self.calls.append(("create", snapshot, pool, target))
        created = SimpleNamespace(id="created", snapshot=snapshot, target=target, pool=pool, current_size=0)
        setattr(created, self.field, self.value)
        self.pools.append(created)
        return created


class ConcurrentCreateClient(Client):
    async def create(self, snapshot, pool, target=None):
        self.calls.append(("create", snapshot, pool, target))
        created = SimpleNamespace(id="created", snapshot=snapshot, target=target, pool=pool, current_size=0)
        contender = SimpleNamespace(id="contender", snapshot=snapshot, target=target, pool=pool, current_size=0)
        self.pools.extend((created, contender))
        return created


class OwnershipStore:
    def __init__(self, ownership=None):
        self.ownership = ownership
        self.find_args: dict[str, object] | None = None

    async def find(self, **kwargs):
        self.find_args = kwargs
        return self.ownership

    async def save(self, ownership):
        self.ownership = ownership
        return ownership


class FailingOwnershipStore(OwnershipStore):
    async def save(self, _ownership):
        raise RuntimeError("storage unavailable")


def _settings(**overrides):
    return Settings(daytona_child_snapshot="fleet-child-v1", **overrides)


def _campaign() -> WarmPoolCampaign:
    return WarmPoolCampaign("test-campaign", 10, 1800, 1, 1)


def test_disabled_plan_does_not_require_child_snapshot() -> None:
    plan = WarmPoolPlan.from_settings(Settings(daytona_warm_pool_enabled=False))

    assert plan.enabled is False
    assert plan.snapshot == ""
    assert plan.desired_size == 0
    assert plan.manifest_sha256 == ""


def test_enabled_plan_still_requires_child_snapshot() -> None:
    with pytest.raises(ValueError, match="FLEET_DAYTONA_CHILD_SNAPSHOT"):
        WarmPoolPlan.from_settings(Settings(daytona_warm_pool_enabled=True, daytona_warm_pool_size=1))


@pytest.mark.parametrize("spend_cap", [float("nan"), float("inf"), float("-inf"), 0.0])
def test_campaign_rejects_non_finite_or_non_positive_spend(spend_cap: float) -> None:
    with pytest.raises(WarmPoolError, match="spend cap"):
        WarmPoolCampaign("campaign", spend_cap, 60, 1, 1).validate()


@pytest.mark.asyncio
async def test_enabled_zero_capacity_is_a_drained_noop_without_provider_create() -> None:
    settings = _settings(daytona_warm_pool_enabled=True, daytona_warm_pool_size=0)
    client = Client()
    result = await reconcile_warm_pool(client, WarmPoolPlan.from_settings(settings), apply=True, campaign=_campaign())
    assert result.action == "drained"
    assert client.calls == [("list",)]


@pytest.mark.parametrize("field", ("volumes", "env_vars", "user", "resources"))
def test_semantic_child_request_rejects_disqualifying_drift(field):
    request = {"snapshot": "child", "pool": 1, "target": "us", field: {"drift": True}}
    with pytest.raises(WarmPoolError, match="cannot include"):
        validate_semantic_child_warm_pool_request(request)


def test_semantic_child_request_validates_pinned_sdk_model_and_extra_fields():
    request = CreateWarmPool(snapshot="child", pool=1, target="us")
    validate_semantic_child_warm_pool_request(request)
    assert request.to_dict() == {"snapshot": "child", "pool": 1, "target": "us"}
    request.additional_properties["env_vars"] = {"SECRET": "drift"}
    with pytest.raises(WarmPoolError, match="cannot include"):
        validate_semantic_child_warm_pool_request(request)


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
    assert client.calls[-2:] == [("create", "fleet-child-v1", 1, "us"), ("list",)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "label"),
    [
        ("snapshot", "other-snapshot", "snapshot"),
        ("target", "eu", "target"),
        ("pool", 2, "desired capacity"),
    ],
)
async def test_create_response_must_match_requested_immutable_definition(field, value, label):
    settings = _settings(daytona_warm_pool_enabled=True, daytona_warm_pool_size=1, daytona_warm_pool_region="us")
    plan = WarmPoolPlan.from_settings(settings)
    client = MismatchedCreateClient(field, value)

    with pytest.raises(WarmPoolError, match=f"mismatched {label}.*cleaned up"):
        await reconcile_warm_pool(
            client,
            plan,
            apply=True,
            campaign=_campaign(),
            ownership_store=OwnershipStore(),
            candidate_sha="a" * 64,
        )

    assert ("delete", "created") in client.calls
    assert client.pools == []


@pytest.mark.asyncio
async def test_create_response_requires_safe_provider_id():
    settings = _settings(daytona_warm_pool_enabled=True, daytona_warm_pool_size=1)
    plan = WarmPoolPlan.from_settings(settings)

    class UnsafeIdClient(Client):
        async def create(self, snapshot, pool, target=None):
            self.calls.append(("create", snapshot, pool, target))
            created = SimpleNamespace(id="created/pool", snapshot=snapshot, target=target, pool=pool, current_size=0)
            self.pools.append(created)
            return created

    client = UnsafeIdClient()
    with pytest.raises(WarmPoolError, match="safe id"):
        await reconcile_warm_pool(
            client,
            plan,
            apply=True,
            campaign=_campaign(),
            ownership_store=OwnershipStore(),
            candidate_sha="a" * 64,
        )
    assert ("delete", "created/pool") in client.calls
    assert client.pools == []


@pytest.mark.asyncio
async def test_successful_create_rejects_and_cleans_up_non_409_concurrent_match():
    settings = _settings(daytona_warm_pool_enabled=True, daytona_warm_pool_size=1, daytona_warm_pool_region="us")
    plan = WarmPoolPlan.from_settings(settings)
    client = ConcurrentCreateClient()

    with pytest.raises(WarmPoolError, match=r"multiple matching pools.*cleaned up"):
        await reconcile_warm_pool(
            client,
            plan,
            apply=True,
            campaign=_campaign(),
            ownership_store=OwnershipStore(),
            candidate_sha="a" * 64,
        )

    assert ("delete", "created") in client.calls
    assert [pool.id for pool in client.pools] == ["contender"]


@pytest.mark.asyncio
async def test_create_conflict_re_reads_and_validates_the_matching_pool() -> None:
    settings = _settings(daytona_warm_pool_enabled=True, daytona_warm_pool_size=1, daytona_warm_pool_region="us")
    plan = WarmPoolPlan.from_settings(settings)
    pool = SimpleNamespace(id="raced-pool", snapshot=plan.snapshot, target=plan.target, pool=1, current_size=1)
    store = OwnershipStore(
        WarmPoolOwnership(
            pool.id,
            "test-campaign",
            plan.snapshot,
            plan.target,
            plan.manifest_sha256,
            "a" * 64,
        )
    )
    result = await reconcile_warm_pool(
        ConflictClient(pool),
        plan,
        apply=True,
        campaign=_campaign(),
        ownership_store=store,
        candidate_sha="a" * 64,
    )
    assert result.action == "unchanged"


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
        WarmPoolOwnership(
            "pool-1", "test-campaign", plan.snapshot, plan.target, plan.manifest_sha256, "a" * 64, 1, "owned"
        )
    )
    assert (await reconcile_warm_pool(client, plan, ownership_store=store, candidate_sha="a" * 64)).action == "update"
    assert store.find_args == {"pool_id": "pool-1"}
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


@pytest.mark.asyncio
async def test_explicit_adoption_can_reactivate_retired_exact_pool() -> None:
    settings = _settings(daytona_warm_pool_enabled=True, daytona_warm_pool_size=1)
    plan = WarmPoolPlan.from_settings(settings)
    pool = SimpleNamespace(id="pool-retired", snapshot=plan.snapshot, target=plan.target, pool=1, current_size=1)
    store = OwnershipStore(
        WarmPoolOwnership(
            pool.id,
            "old-campaign",
            plan.snapshot,
            plan.target,
            plan.manifest_sha256,
            "a" * 64,
            status="retired",
        )
    )

    with pytest.raises(WarmPoolError, match="durable warm-pool ownership"):
        await reconcile_warm_pool(Client([pool]), plan, apply=True, campaign=_campaign(), ownership_store=store)

    result = await reconcile_warm_pool(
        Client([pool]),
        plan,
        apply=True,
        campaign=_campaign(),
        ownership_store=store,
        candidate_sha="b" * 64,
        adopt=True,
    )

    assert result.action == "unchanged"
    assert store.ownership is not None
    assert store.ownership.status == "owned"
    assert store.ownership.candidate_sha == "b" * 64
