"""Explicit, operator-owned reconciliation for clean SemanticChild warm pools.

This module deliberately has no Turn/runtime imports.  A user Run can consume a
provider-claimed warm sandbox through the ordinary child creation path, but it
cannot create, resize, or drain organization-wide capacity.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.provisioning import DaytonaEnvironmentProfile, DaytonaSandboxSpec, environment_manifest


class WarmPoolError(ValueError):
    """Raised when a requested pool is unsafe to reconcile."""


class WarmPoolClient(Protocol):
    async def list(self) -> Sequence[Any]: ...

    async def create(self, snapshot: str, pool: int, target: str | None = None) -> Any: ...

    async def update(self, warm_pool_id: str, pool: int) -> Any: ...

    async def delete(self, warm_pool_id: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class WarmPoolOwnership:
    pool_id: str
    campaign: str
    snapshot: str
    target: str | None
    manifest_sha256: str
    candidate_sha: str


class WarmPoolOwnershipStore(Protocol):
    async def find(self, *, snapshot: str, target: str | None) -> WarmPoolOwnership | None: ...

    async def save(self, ownership: WarmPoolOwnership) -> WarmPoolOwnership: ...


@dataclass(frozen=True, slots=True)
class WarmPoolCampaign:
    """Explicit operator limits required before a live capacity operation."""

    name: str
    spend_cap: float
    elapsed_seconds: int
    admission_limit: int
    sandbox_concurrency: int

    def validate(self) -> None:
        if not self.name.strip() or len(self.name) > 128:
            raise WarmPoolError("a bounded campaign name is required")
        if min(self.spend_cap, self.elapsed_seconds, self.admission_limit, self.sandbox_concurrency) <= 0:
            raise WarmPoolError("campaign spend, elapsed, admission, and concurrency limits must be positive")


@dataclass(frozen=True, slots=True)
class WarmPoolPlan:
    snapshot: str
    target: str | None
    desired_size: int
    enabled: bool
    manifest_sha256: str

    @classmethod
    def from_settings(cls, settings: Settings) -> WarmPoolPlan:
        spec = DaytonaSandboxSpec.from_settings(settings, DaytonaEnvironmentProfile.SEMANTIC_CHILD)
        manifest = environment_manifest(spec)
        if not manifest.warm_pool_eligible or manifest.volume_allowed:
            raise WarmPoolError("only the clean SemanticChild profile may use a warm pool")
        target = (settings.daytona_warm_pool_region or "").strip() or None
        return cls(
            snapshot=spec.snapshot,
            target=target,
            desired_size=settings.daytona_warm_pool_size,
            enabled=settings.daytona_warm_pool_enabled,
            manifest_sha256=manifest.digest,
        )


@dataclass(frozen=True, slots=True)
class WarmPoolResult:
    action: str
    pool_id: str | None
    desired_size: int
    current_size: int | None
    warm_hit_status: str = "unknown"


def _value(pool: Any, name: str) -> str | None:
    value = getattr(pool, name, None)
    if value is None and isinstance(pool, dict):
        value = pool.get(name)
    return str(value) if value is not None else None


def _matching_pools(pools: Sequence[Any], plan: WarmPoolPlan) -> list[Any]:
    return [
        pool for pool in pools if _value(pool, "snapshot") == plan.snapshot and _value(pool, "target") == plan.target
    ]


async def _cleanup_created_pool(client: WarmPoolClient, pool_id: str) -> bool:
    """Delete and re-list one pool, confirming provider absence before failure."""
    try:
        await client.delete(pool_id)
        return all(_value(pool, "id") != pool_id for pool in await client.list())
    except Exception:
        return False


async def reconcile_warm_pool(
    client: WarmPoolClient,
    plan: WarmPoolPlan,
    *,
    apply: bool = False,
    campaign: WarmPoolCampaign | None = None,
    ownership_store: WarmPoolOwnershipStore | None = None,
    candidate_sha: str | None = None,
    adopt: bool = False,
) -> WarmPoolResult:
    """Plan, check, or reconcile one explicitly configured pool.

    Disabled policy is a no-op.  Multiple matching pools are unsafe because
    Fleet cannot establish sole ownership.  A matching pool without Fleet's
    ownership label is also left untouched; the current Daytona SDK warm-pool
    object does not expose label mutation, so adoption remains an explicit
    future operator action.
    """
    if not plan.enabled:
        return WarmPoolResult("disabled", None, 0, None)
    if candidate_sha is not None and not re.fullmatch(r"[0-9a-f]{64}", candidate_sha):
        raise WarmPoolError("candidate SHA must be a 64-character hexadecimal digest")
    if apply:
        if campaign is None:
            raise WarmPoolError("a campaign preflight is required for reconciliation")
        campaign.validate()
    pools = _matching_pools(await client.list(), plan)
    if len(pools) > 1:
        raise WarmPoolError("multiple matching Daytona warm pools require manual reconciliation")
    existing = pools[0] if pools else None
    if existing is None:
        if not apply:
            return WarmPoolResult("create", None, plan.desired_size, 0)
        created = await client.create(plan.snapshot, plan.desired_size, plan.target)
        created_id = _value(created, "id")
        if not created_id:
            raise WarmPoolError("created Daytona warm pool did not expose an id")
        if ownership_store is None or campaign is None or candidate_sha is None:
            raise WarmPoolError("durable ownership, campaign, and candidate identity are required")
        try:
            await ownership_store.save(
                WarmPoolOwnership(
                    created_id,
                    campaign.name,
                    plan.snapshot,
                    plan.target,
                    plan.manifest_sha256,
                    candidate_sha,
                )
            )
        except Exception as exc:
            if not await _cleanup_created_pool(client, created_id):
                raise WarmPoolError(
                    "durable ownership failed and provider pool cleanup could not be confirmed"
                ) from exc
            raise WarmPoolError("durable ownership failed; newly-created provider pool was cleaned up") from exc
        return WarmPoolResult("created", created_id, plan.desired_size, int(getattr(created, "current_size", 0)))
    pool_id = _value(existing, "id")
    if not pool_id:
        raise WarmPoolError("matching Daytona warm pool did not expose an id")
    ownership = await ownership_store.find(snapshot=plan.snapshot, target=plan.target) if ownership_store else None
    if ownership is None and not adopt:
        raise WarmPoolError("matching Daytona warm pool is not Fleet-owned; explicit adoption is required")
    if ownership is not None and ownership.pool_id != pool_id:
        raise WarmPoolError("durable warm-pool ownership does not match the provider pool")
    if ownership is not None and (
        ownership.manifest_sha256 != plan.manifest_sha256
        or (candidate_sha is not None and ownership.candidate_sha != candidate_sha)
    ):
        raise WarmPoolError("durable warm-pool ownership does not match the current candidate or manifest")
    if adopt:
        if not apply or ownership_store is None or campaign is None or candidate_sha is None:
            raise WarmPoolError("adoption requires apply, durable ownership, campaign, and candidate identity")
        await ownership_store.save(
            WarmPoolOwnership(pool_id, campaign.name, plan.snapshot, plan.target, plan.manifest_sha256, candidate_sha)
        )
    current = getattr(existing, "pool", None)
    if not isinstance(current, int):
        raise WarmPoolError("matching Daytona warm pool did not expose its desired capacity")
    current_size = getattr(existing, "current_size", None)
    if current == plan.desired_size:
        return WarmPoolResult("unchanged", pool_id, current, current_size if isinstance(current_size, int) else None)
    if not apply:
        return WarmPoolResult(
            "update", pool_id, plan.desired_size, current_size if isinstance(current_size, int) else None
        )
    updated = await client.update(pool_id, plan.desired_size)
    updated_size = getattr(updated, "current_size", None)
    return WarmPoolResult(
        "updated", pool_id, plan.desired_size, updated_size if isinstance(updated_size, int) else None
    )


__all__ = [
    "WarmPoolCampaign",
    "WarmPoolError",
    "WarmPoolOwnership",
    "WarmPoolOwnershipStore",
    "WarmPoolPlan",
    "WarmPoolResult",
    "reconcile_warm_pool",
]
