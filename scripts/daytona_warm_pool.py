"""Plan, inspect, or explicitly reconcile Fleet's clean child warm pool."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from fleet_rlm.config.loader import load_runtime_settings, require_live_execution
from fleet_rlm.daytona.platform import build_daytona_client
from fleet_rlm.daytona.warm_pool import WarmPoolCampaign, WarmPoolError, WarmPoolPlan, reconcile_warm_pool
from fleet_rlm.persistence.database import (
    create_async_engine_from_url,
    create_session_factory,
    ensure_database_compatible,
)
from fleet_rlm.persistence.repositories import SqlAlchemyWarmPoolOwnershipStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "check", "reconcile"))
    parser.add_argument("--campaign")
    parser.add_argument("--spend-cap", type=float)
    parser.add_argument("--elapsed-seconds", type=int)
    parser.add_argument("--admission-limit", type=int)
    parser.add_argument("--sandbox-concurrency", type=int)
    parser.add_argument("--adopt", action="store_true")
    return parser


def _campaign(args: argparse.Namespace) -> WarmPoolCampaign | None:
    values = (args.campaign, args.spend_cap, args.elapsed_seconds, args.admission_limit, args.sandbox_concurrency)
    if not any(value is not None for value in values):
        return None
    if any(value is None for value in values):
        raise WarmPoolError("campaign, spend cap, elapsed, admission, and concurrency limits are all required")
    return WarmPoolCampaign(
        name=args.campaign,
        spend_cap=args.spend_cap,
        elapsed_seconds=args.elapsed_seconds,
        admission_limit=args.admission_limit,
        sandbox_concurrency=args.sandbox_concurrency,
    )


def _require_operator_preflight(args: argparse.Namespace, plan: WarmPoolPlan) -> WarmPoolCampaign:
    """Require bounded campaign limits for an enabled provider operation."""
    campaign = _campaign(args)
    if campaign is None:
        raise WarmPoolError("enabled warm-pool operations require a complete campaign preflight")
    campaign.validate()
    if not plan.target:
        raise WarmPoolError("check and reconcile require an explicit warm-pool region or target")
    return campaign


async def _run(args: argparse.Namespace) -> dict[str, object]:
    command = args.command
    # Resolve the policy before requiring the live switch so the committed
    # disabled/zero-capacity check remains a safe local no-op. Provider access
    # is gated only after an enabled policy has been established.
    settings = load_runtime_settings()
    plan = WarmPoolPlan.from_settings(settings)
    if command == "plan":
        return {"action": "plan", **asdict(plan)}
    # The committed policy is disabled/zero by default. A read-only check must
    # preserve that safe no-op and remain usable without campaign credentials
    # or limits. Mutating reconciliation is the only path that requires the
    # full operator preflight.
    if not plan.enabled:
        return {
            "action": "disabled",
            "pool_id": None,
            "desired_size": 0,
            "current_size": None,
            "warm_hit_status": "unknown",
            "snapshot": plan.snapshot,
            "target": plan.target,
            "manifest_sha256": plan.manifest_sha256,
        }
    settings = require_live_execution()
    # A read-only provider check still consumes operator/provider quota and
    # must be attributable to a bounded campaign.  The disabled policy exits
    # above so local plan/check remain safe no-ops when capacity is off.
    campaign = _require_operator_preflight(args, plan)
    client = build_daytona_client(settings)
    engine = create_async_engine_from_url(settings.database_url or "")
    try:
        await ensure_database_compatible(settings.database_url or "", repo_root=Path(__file__).resolve().parents[1])
        ownership_store = SqlAlchemyWarmPoolOwnershipStore(create_session_factory(engine))
        candidate_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        result = await reconcile_warm_pool(
            client.warm_pool,
            plan,
            apply=command == "reconcile",
            campaign=campaign,
            ownership_store=ownership_store,
            candidate_sha=candidate_sha,
            adopt=args.adopt,
        )
    finally:
        await client.close()
        await engine.dispose()
    return {
        "action": result.action,
        "pool_id": result.pool_id,
        "desired_size": result.desired_size,
        "current_size": result.current_size,
        "warm_hit_status": result.warm_hit_status,
        "snapshot": plan.snapshot,
        "target": plan.target,
        "manifest_sha256": plan.manifest_sha256,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except Exception:
        print("Daytona warm-pool operation could not be completed safely.")
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
