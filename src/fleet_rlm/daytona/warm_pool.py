"""Explicit, operator-owned reconciliation for clean SemanticChild warm pools.

This module deliberately has no Turn/runtime imports.  A user Run can consume a
provider-claimed warm sandbox through the ordinary child creation path, but it
cannot create, resize, or drain organization-wide capacity.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.errors import provider_status_code
from fleet_rlm.daytona.provisioning import DaytonaEnvironmentProfile, DaytonaSandboxSpec, environment_manifest


class WarmPoolError(ValueError):
    """Raised when a requested pool is unsafe to reconcile."""


def validate_semantic_child_warm_pool_request(request: Mapping[str, Any] | Any) -> None:
    """Reject provider request drift that would make a pool ineligible.

    The pinned SDK's ``CreateWarmPool`` wire model only has snapshot, pool and
    target. Keep this boundary explicit so a future SDK/client adapter cannot
    silently add volumes, environment, users, secrets, or resource overrides.
    """

    def value(name: str) -> Any:
        if isinstance(request, Mapping):
            return request.get(name)
        return getattr(request, name, None)

    forbidden = {
        "volume": value("volume"),
        "volumes": value("volumes"),
        "volume_mounts": value("volume_mounts"),
        "env": value("env"),
        "env_vars": value("env_vars"),
        "environment": value("environment"),
        "custom_env": value("custom_env"),
        "secrets": value("secrets"),
        "user": value("user"),
        "os_user": value("os_user"),
        "resources": value("resources"),
        "cpu": value("cpu"),
        "memory": value("memory"),
        "disk": value("disk"),
        "gpu": value("gpu"),
    }
    additional = value("additional_properties")
    if isinstance(additional, Mapping):
        forbidden.update({str(key): item for key, item in additional.items()})
    if any(item not in (None, "", False, [], {}, ()) for item in forbidden.values()):
        raise WarmPoolError("SemanticChild warm-pool requests cannot include volume, env, user, or resource overrides")


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
    reconciliation_generation: int = 1
    status: str = "owned"

    def __post_init__(self) -> None:
        if type(self.reconciliation_generation) is not int or self.reconciliation_generation < 1:
            raise WarmPoolError("warm-pool ownership generation must be positive")
        if self.status not in {"owned", "retired"}:
            raise WarmPoolError("warm-pool ownership status is unsupported")


class WarmPoolOwnershipStore(Protocol):
    async def find(self, *, pool_id: str) -> WarmPoolOwnership | None: ...

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
        if (
            isinstance(self.spend_cap, bool)
            or not isinstance(self.spend_cap, (int, float))
            or not math.isfinite(float(self.spend_cap))
            or self.spend_cap <= 0
        ):
            raise WarmPoolError("campaign spend cap must be finite and positive")
        if any(
            type(value) is not int or value <= 0
            for value in (self.elapsed_seconds, self.admission_limit, self.sandbox_concurrency)
        ):
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
        enabled = bool(settings.daytona_warm_pool_enabled)
        target = (settings.daytona_warm_pool_region or "").strip() or None
        if not enabled:
            # Disabled capacity is a local policy no-op. In particular, it
            # must remain inspectable when the optional child snapshot has not
            # been configured at all. Do not construct a SandboxSpec (which
            # intentionally validates that required snapshot for enabled use).
            snapshot = settings.daytona_child_snapshot
            return cls(
                snapshot=snapshot.strip() if isinstance(snapshot, str) else "",
                target=target,
                desired_size=0,
                enabled=False,
                manifest_sha256="",
            )
        spec = DaytonaSandboxSpec.from_settings(settings, DaytonaEnvironmentProfile.SEMANTIC_CHILD)
        manifest = environment_manifest(spec)
        if not manifest.warm_pool_eligible or manifest.volume_allowed:
            raise WarmPoolError("only the clean SemanticChild profile may use a warm pool")
        return cls(
            snapshot=spec.snapshot,
            target=target,
            desired_size=settings.daytona_warm_pool_size,
            enabled=True,
            manifest_sha256=manifest.digest,
        )


@dataclass(frozen=True, slots=True)
class WarmPoolResult:
    action: str
    pool_id: str | None
    desired_size: int
    current_size: int | None
    warm_hit_status: str = "unknown"


_MISSING = object()
_SAFE_POOL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,254}\Z")


def _raw_value(pool: Any, name: str) -> Any:
    if isinstance(pool, Mapping):
        return pool.get(name, _MISSING)
    return getattr(pool, name, _MISSING)


def _value(pool: Any, name: str) -> str | None:
    value = _raw_value(pool, name)
    return str(value) if value is not _MISSING and value is not None else None


def _safe_pool_id(pool: Any) -> str | None:
    value = _raw_value(pool, "id")
    if not isinstance(value, str) or _SAFE_POOL_ID.fullmatch(value) is None:
        return None
    return value


def _created_pool_mismatch(created: Any, plan: WarmPoolPlan) -> str | None:
    """Return why a provider create response is not the requested pool."""
    if _raw_value(created, "snapshot") != plan.snapshot:
        return "snapshot"
    if _raw_value(created, "target") != plan.target:
        return "target"
    returned_pool = _raw_value(created, "pool")
    if isinstance(returned_pool, bool) or returned_pool != plan.desired_size:
        return "desired capacity"
    return None


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
        # The pinned Daytona SDK rejects ``pool=0`` at DTO validation time.
        # A zero-size enabled policy is nevertheless useful as an explicit
        # drained state, so do not manufacture a provider request when there
        # is no existing pool to drain.
        if plan.desired_size == 0:
            return WarmPoolResult("drained", None, 0, 0)
        if not apply:
            return WarmPoolResult("create", None, plan.desired_size, 0)
        # Construct the pinned SDK DTO here, at the provider boundary. This
        # keeps validation coupled to the exact serialized model used by the
        # SDK rather than to a look-alike local dictionary.
        from daytona_api_client_async import CreateWarmPool

        request = CreateWarmPool(snapshot=plan.snapshot, pool=plan.desired_size, target=plan.target)
        validate_semantic_child_warm_pool_request(request)
        try:
            created = await client.create(request.snapshot, int(request.pool), request.target)
        except Exception as exc:
            # A concurrent operator may have created the same definition after
            # our initial list. Re-read and validate the exact definition
            # instead of treating a provider conflict as an unowned success.
            if provider_status_code(exc) != 409:
                raise
            reread = _matching_pools(await client.list(), plan)
            if len(reread) != 1:
                raise WarmPoolError("warm-pool create conflict could not be resolved to one matching pool") from exc
            existing = reread[0]
        else:
            created_id = _safe_pool_id(created)
            mismatch = _created_pool_mismatch(created, plan)
            if created_id is None:
                # The provider response is not safe to persist, but a
                # non-empty string can still be passed to the typed provider
                # delete operation so the exact response is not leaked.
                raw_created_id = _raw_value(created, "id")
                if (
                    isinstance(raw_created_id, str)
                    and raw_created_id
                    and not await _cleanup_created_pool(client, raw_created_id)
                ):
                    raise WarmPoolError(
                        "created Daytona warm pool did not expose a safe id and cleanup could not be confirmed"
                    )
                raise WarmPoolError("created Daytona warm pool did not expose a safe id")
            if mismatch is not None:
                if not await _cleanup_created_pool(client, created_id):
                    raise WarmPoolError(
                        f"created Daytona warm pool returned mismatched {mismatch} and cleanup could not be confirmed"
                    )
                raise WarmPoolError(f"created Daytona warm pool returned mismatched {mismatch}; it was cleaned up")
            # A provider may accept concurrent identical creates without a
            # 409. Re-list after success and clean up exactly our response if
            # more than one immutable match is now visible.
            reread = _matching_pools(await client.list(), plan)
            if len(reread) > 1:
                if not await _cleanup_created_pool(client, created_id):
                    raise WarmPoolError(
                        "concurrent warm-pool creates produced multiple matches and cleanup could not be confirmed"
                    )
                raise WarmPoolError(
                    "concurrent warm-pool creates produced multiple matching pools; newly-created pool was cleaned up"
                )
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
    # Provider matching narrows the candidate set by immutable snapshot and
    # target. Durable authority must still be proved against this exact pool
    # identity, never inferred from a prior record with the same definition.
    ownership = await ownership_store.find(pool_id=pool_id) if ownership_store else None
    if ownership is None and not adopt:
        raise WarmPoolError("matching Daytona warm pool is not Fleet-owned; explicit adoption is required")
    definition_matches = ownership is None or (
        ownership.snapshot == plan.snapshot
        and ownership.target == plan.target
        and ownership.manifest_sha256 == plan.manifest_sha256
    )
    candidate_matches = ownership is None or (candidate_sha is None or ownership.candidate_sha == candidate_sha)
    if (
        ownership is not None
        and (not definition_matches or not candidate_matches or ownership.status != "owned")
        and not adopt
    ):
        raise WarmPoolError("durable warm-pool ownership does not match the current candidate or manifest")
    if adopt:
        if not apply or ownership_store is None or campaign is None or candidate_sha is None:
            raise WarmPoolError("adoption requires apply, durable ownership, campaign, and candidate identity")
        if not definition_matches:
            raise WarmPoolError("adoption cannot change the immutable warm-pool definition")
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
    "validate_semantic_child_warm_pool_request",
]
