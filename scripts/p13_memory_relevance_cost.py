"""Measure live Daytona Workspace Memory relevance composition cost."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from dotenv import load_dotenv

from fleet_rlm.config import require_live_execution
from fleet_rlm.daytona.dspy_sync_bridge import sync_sandbox
from fleet_rlm.daytona.platform import LiveDaytonaPlatform, LiveDaytonaVolumeClient, build_daytona_client
from fleet_rlm.daytona.provisioning import (
    sandbox_spec_from_settings,
    volume_config_from_settings,
    volume_mount_spec,
)
from fleet_rlm.daytona.workspace_memory import DaytonaWorkspaceMemoryStore, read_workspace_memory_injection_digest
from fleet_rlm.files.memory_models import workspace_memory_record_id

REPO_ROOT = Path(__file__).resolve().parents[1]
P7_RUN_PREPARATION_P95_SECONDS = 17.453666


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=5)
    return parser


def _record(timestamp: str, category: str, learning: str) -> str:
    memory_id = workspace_memory_record_id(timestamp, category, learning)
    return f"- [{timestamp}] **{category}** <!-- id:{memory_id} -->: {learning}\n"


async def _run(output: Path, samples: int) -> int:
    settings = require_live_execution()
    volume_name = f"fleet-memory-relevance-{uuid4()}"
    settings = settings.model_copy(update={"volume_name": volume_name})
    client = build_daytona_client(settings)
    spec = sandbox_spec_from_settings(settings)
    platform = LiveDaytonaPlatform(client, spec)
    volume_client = LiveDaytonaVolumeClient(client)
    sandbox = None
    sandbox_created = 0
    volume_deleted = False
    try:
        volume = await volume_client.get(volume_name, create=True)
        volume_config = volume_config_from_settings(settings)
        workspace_id: UUID = uuid4()
        mount = volume_mount_spec(volume_config, str(volume.id), workspace_id=workspace_id)
        sandbox = await platform.create(
            volume_id=volume.id,
            mount_path=mount["mount_path"],
            volume_subpath=mount["subpath"],
            labels={
                "fleet-package": "fleet_rlm",
                "purpose": "memory-relevance-cost",
                "workspace_id": str(workspace_id),
            },
            ephemeral=True,
        )
        sandbox_created = 1
        bridged_sandbox = sync_sandbox(sandbox, asyncio.get_running_loop())
        store = DaytonaWorkspaceMemoryStore(
            bridged_sandbox,
            volume_paths=volume_config.paths(),
            max_upload_bytes=settings.max_upload_bytes,
        )
        old_timestamp = "2026-07-19T09:00:00Z"
        old_category = "Preference"
        old_learning = "Prefers polars for dataframe joins and concise reports."
        old_record = _record(old_timestamp, old_category, old_learning)
        seed_started = time.perf_counter()

        def seed_corpus() -> None:
            store.append_record(old_record)
            for index in range(48):
                ts = f"2026-07-26T14:{index // 60:02d}:{index % 60:02d}Z"
                learning = f"Unrelated deployment and UI note {index:03d}."
                store.append_record(_record(ts, "Ops", learning))

        await asyncio.to_thread(seed_corpus)
        seed_duration = time.perf_counter() - seed_started

        recent_times: list[float] = []
        injection_times: list[float] = []
        tail_result = None
        relevant_digest = unrelated_digest = ""
        for _ in range(samples):
            started = time.perf_counter()
            tail_result = await asyncio.to_thread(store.read_tail, byte_budget=4096)
            recent_times.append(time.perf_counter() - started)
            started = time.perf_counter()
            relevant_digest = await asyncio.to_thread(
                read_workspace_memory_injection_digest,
                store,
                request="How should this workspace write dataframe joins?",
            )
            injection_times.append(time.perf_counter() - started)
        unrelated_digest = await asyncio.to_thread(
            read_workspace_memory_injection_digest,
            store,
            request="List the deployment rollout notes.",
        )
        assert relevant_digest and old_learning in relevant_digest
        assert tail_result is not None and old_learning not in tail_result.content
        assert old_learning not in unrelated_digest
        assert len(relevant_digest.encode("utf-8")) <= 4096
        relevant_p95 = (
            statistics.quantiles(injection_times, n=100)[94] if len(injection_times) > 1 else injection_times[0]
        )
        recent_p95 = statistics.quantiles(recent_times, n=100)[94] if len(recent_times) > 1 else recent_times[0]
        overhead_p95 = max(0.0, relevant_p95 - recent_p95)
        guard_seconds = P7_RUN_PREPARATION_P95_SECONDS * 0.10
        receipt = {
            "schema": "fleet.p13-memory-relevance-cost/v1",
            "volume_name": volume_name,
            "samples": samples,
            "seed_duration_seconds": round(seed_duration, 3),
            "recent_tail_p95_seconds": round(recent_p95, 6),
            "relevance_injection_p95_seconds": round(relevant_p95, 6),
            "relevance_overhead_p95_seconds": round(overhead_p95, 6),
            "p7_run_preparation_p95_seconds": P7_RUN_PREPARATION_P95_SECONDS,
            "guard_seconds": round(guard_seconds, 3),
            "guard_passed": overhead_p95 <= guard_seconds,
            "sandbox_created_count": sandbox_created,
            "no_additional_sandbox_for_search": sandbox_created == 1,
            "old_preference_injected": old_learning in relevant_digest,
            "old_preference_absent_from_recency_tail": tail_result is not None
            and old_learning not in tail_result.content,
            "unrelated_request_recency_only": old_learning not in unrelated_digest,
            "evidence_sha256": hashlib.sha256(
                json.dumps({"recent": recent_times, "injection": injection_times}, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "finished_at": datetime.now(UTC).isoformat(),
        }
        if not receipt["guard_passed"]:
            return 3
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    finally:
        if sandbox is not None:
            await platform.delete(sandbox)
        try:
            await client.volume.delete(volume)
            volume_deleted = True
        except Exception:
            volume_deleted = False
        close = getattr(client, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
        print(json.dumps({"sandbox_deleted": sandbox is not None, "volume_deleted": volume_deleted}, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    load_dotenv(REPO_ROOT / ".env", override=False)
    args = _parser().parse_args(argv)
    if args.samples < 1 or args.samples > 64:
        print("samples must be between 1 and 64")
        return 2
    return asyncio.run(_run(args.output.expanduser().resolve(), args.samples))


if __name__ == "__main__":
    raise SystemExit(main())
