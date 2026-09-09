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


async def _run(args: argparse.Namespace) -> dict[str, object]:
    command = args.command
    settings = load_runtime_settings() if command == "plan" else require_live_execution()
    plan = WarmPoolPlan.from_settings(settings)
    if command == "plan":
        return {"action": "plan", **asdict(plan)}
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
            campaign=_campaign(args),
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
    except (WarmPoolError, ValueError):
        print("Daytona warm-pool operation could not be completed safely.")
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
