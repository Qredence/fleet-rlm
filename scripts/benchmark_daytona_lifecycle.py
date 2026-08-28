"""Benchmark the complete Fleet Daytona Turn-Sandbox startup boundary.

This is an explicit live operator command. It never prints credentials or raw
provider errors and it does not change Fleet's runtime mode by itself.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.metadata
import inspect
import json
import math
import os
import platform as host_platform
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fleet_rlm.daytona.broker import sync_sandbox
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm.daytona.platform import (
    LiveDaytonaPlatform,
    LiveDaytonaVolumeClient,
    build_daytona_client,
    sandbox_state,
)
from fleet_rlm.daytona.provisioning import (
    ExpectedWorkspaceMount,
    ensure_volume_layout,
    get_or_create_volume_id,
    sandbox_spec_from_settings,
    verify_sandbox_spec,
    verify_sandbox_workspace_mount,
    volume_config_from_settings,
    volume_mount_spec,
)

RECEIPT_SCHEMA = "fleet.daytona-lifecycle-benchmark/v1"
WARMUP_CYCLES = 3
MEASURED_CYCLES = 20
PER_TURN_P95_SECONDS = 10.0
CREATE_TO_FIRST_EXECUTION_PHASES = (
    "volume_readiness",
    "sandbox_create_running",
    "snapshot_mount_user_verification",
    "canonical_layout",
    "interpreter_broker_startup",
    "first_execution",
)
_ALL_PHASES = (*CREATE_TO_FIRST_EXECUTION_PHASES, "shutdown_and_deletion")
_MAX_PUBLIC_VALUE_CHARS = 160


def percentile(values: Sequence[float], percent: int) -> float:
    """Return a deterministic nearest-rank percentile."""
    if not values:
        raise ValueError("percentile requires at least one value")
    if percent < 1 or percent > 100:
        raise ValueError("percent must be between 1 and 100")
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(percent / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize_samples(samples: Sequence[Mapping[str, float]]) -> dict[str, dict[str, float]]:
    """Summarize bounded phase samples without provider payloads."""
    if not samples:
        raise ValueError("benchmark requires measured samples")
    summary: dict[str, dict[str, float]] = {}
    for phase in _ALL_PHASES:
        values = [float(sample[phase]) for sample in samples]
        summary[phase] = {
            "min_seconds": round(min(values), 6),
            "median_seconds": round(percentile(values, 50), 6),
            "p95_seconds": round(percentile(values, 95), 6),
            "max_seconds": round(max(values), 6),
        }
    end_to_end = [sum(float(sample[phase]) for phase in CREATE_TO_FIRST_EXECUTION_PHASES) for sample in samples]
    summary["create_through_first_execution"] = {
        "min_seconds": round(min(end_to_end), 6),
        "median_seconds": round(percentile(end_to_end, 50), 6),
        "p95_seconds": round(percentile(end_to_end, 95), 6),
        "max_seconds": round(max(end_to_end), 6),
    }
    return summary


def benchmark_decision(*, p95_seconds: float, deleted: int, measured: int) -> str:
    """Apply the approved lifecycle decision rule exactly."""
    if p95_seconds <= PER_TURN_P95_SECONDS and measured == MEASURED_CYCLES and deleted == measured:
        return "per_turn"
    return "retained_session"


async def _timed(operation: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    value = operation()
    if inspect.isawaitable(value):
        value = await value
    return value, time.perf_counter() - started


def _bounded(value: object) -> str | None:
    if value is None:
        return None
    text = str(getattr(value, "value", value)).strip()
    if not text:
        return None
    return text[:_MAX_PUBLIC_VALUE_CHARS]


def _versions() -> dict[str, str]:
    versions = {
        "python": sys.version.split()[0],
        "platform": host_platform.system().lower(),
    }
    for package in ("daytona", "dspy", "fleet-rlm"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "unknown"
    return versions


def _expected_mount(volume_config: Any, volume_id: str, workspace_id: Any) -> ExpectedWorkspaceMount:
    mount = volume_mount_spec(volume_config, volume_id, workspace_id=workspace_id)
    return ExpectedWorkspaceMount(
        volume_id=mount["volume_id"],
        volume_subpath=mount["subpath"],
        mount_path=mount["mount_path"],
        workspace_id=workspace_id,
    )


async def _run_cycle(
    *,
    platform: LiveDaytonaPlatform,
    volume_client: LiveDaytonaVolumeClient,
    volume_config: Any,
    sandbox_spec: Any,
    workspace_id: Any,
) -> tuple[dict[str, float], bool, str | None]:
    sample: dict[str, float] = {}
    sandbox: Any | None = None
    interpreter: DaytonaCodeInterpreter | None = None
    deleted = False
    region: str | None = None
    try:
        volume_id, sample["volume_readiness"] = await _timed(
            lambda: get_or_create_volume_id(volume_client, volume_config)
        )
        expected = _expected_mount(volume_config, volume_id, workspace_id)

        async def create_running() -> Any:
            created = await platform.create(
                volume_id=expected.volume_id,
                mount_path=expected.mount_path,
                volume_subpath=expected.volume_subpath,
                labels={
                    "fleet-package": "fleet_rlm",
                    "purpose": "lifecycle-benchmark",
                    "workspace_id": str(workspace_id),
                },
                ephemeral=True,
            )
            if sandbox_state(created) != "running":
                await platform.start(str(created.id))
                refreshed = await platform.get(str(created.id))
                if refreshed is None or sandbox_state(refreshed) != "running":
                    raise RuntimeError("Sandbox did not reach running state")
                return refreshed
            return created

        sandbox, sample["sandbox_create_running"] = await _timed(create_running)
        region = _bounded(getattr(sandbox, "region", None))
        loop = asyncio.get_running_loop()

        def verify() -> None:
            bridge = sync_sandbox(sandbox, loop)
            verify_sandbox_spec(sandbox, sandbox_spec)
            verify_sandbox_workspace_mount(sandbox, expected)
            result = bridge.process.code_run("import getpass; print(getpass.getuser())")
            if str(getattr(result, "result", "") or "").strip() != "daytona":
                raise RuntimeError("Sandbox user did not match Fleet contract")

        _, sample["snapshot_mount_user_verification"] = await _timed(lambda: asyncio.to_thread(verify))
        session_id = uuid4()
        run_id = uuid4()
        _, sample["canonical_layout"] = await _timed(
            lambda: ensure_volume_layout(
                sandbox,
                volume_config.paths(),
                session_id=session_id,
                run_id=run_id,
            )
        )
        interpreter = DaytonaCodeInterpreter(
            backend=sandbox_backend(sandbox, loop=loop),
            tools={"benchmark_ping": lambda: "pong"},
        )
        _, sample["interpreter_broker_startup"] = await _timed(lambda: asyncio.to_thread(interpreter.execute, "pass"))
        result, sample["first_execution"] = await _timed(
            lambda: asyncio.to_thread(interpreter.execute, "print('fleet-benchmark-ok')")
        )
        if "fleet-benchmark-ok" not in str(result):
            raise RuntimeError("First interpreter execution did not return the marker")
        return sample, False, region
    finally:
        cleanup_started = time.perf_counter()
        if interpreter is not None:
            with contextlib.suppress(Exception):
                interpreter.shutdown()
        if sandbox is not None:
            try:
                await platform.delete(sandbox)
                deleted = True
            except Exception:
                deleted = False
        sample["shutdown_and_deletion"] = time.perf_counter() - cleanup_started
        sample["_deleted"] = 1.0 if deleted else 0.0


async def run_benchmark(settings: Any) -> dict[str, object]:
    """Run warmups and measured cycles, returning only bounded evidence."""
    client = build_daytona_client(settings)
    spec = sandbox_spec_from_settings(settings)
    platform = LiveDaytonaPlatform(client, spec)
    volume_client = LiveDaytonaVolumeClient(client)
    volume_config = volume_config_from_settings(settings)
    workspace_id = uuid4()
    measured: list[dict[str, float]] = []
    deleted = 0
    region: str | None = None
    failure: dict[str, object] | None = None
    started_at = datetime.now(UTC).isoformat()
    try:
        for index in range(WARMUP_CYCLES + MEASURED_CYCLES):
            try:
                sample, _, cycle_region = await _run_cycle(
                    platform=platform,
                    volume_client=volume_client,
                    volume_config=volume_config,
                    sandbox_spec=spec,
                    workspace_id=workspace_id,
                )
            except Exception as exc:
                failure = {
                    "cycle": index + 1,
                    "stage": "warmup" if index < WARMUP_CYCLES else "measured",
                    "error_type": type(exc).__name__,
                }
                break
            if cycle_region and region is None:
                region = cycle_region
            if index >= WARMUP_CYCLES:
                deleted += int(sample.pop("_deleted", 0.0))
                measured.append(sample)
        summary = summarize_samples(measured) if measured else {}
        p95 = float(summary.get("create_through_first_execution", {}).get("p95_seconds", math.inf))
        decision = benchmark_decision(p95_seconds=p95, deleted=deleted, measured=len(measured))
        return {
            "schema": RECEIPT_SCHEMA,
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "versions": _versions(),
            "snapshot": spec.snapshot,
            "region": region,
            "cycles": {
                "warmups_required": WARMUP_CYCLES,
                "measured_required": MEASURED_CYCLES,
                "measured_completed": len(measured),
                "deleted_successfully": deleted,
            },
            "threshold": {
                "create_through_first_execution_p95_seconds": PER_TURN_P95_SECONDS,
                "requires_all_measured_deletions": True,
            },
            "samples_seconds": measured,
            "summary": summary,
            "decision": decision,
            "failure": failure,
        }
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Ignored or out-of-repository path for the bounded JSON receipt.",
    )
    return parser


async def _run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        raise SystemExit("FLEET_LIVE=1 is required")
        from fleet_rlm.config.loader import load_runtime_settings

    settings = load_runtime_settings()
    if settings.daytona_api_key is None or not settings.daytona_api_key.get_secret_value().strip():
        raise SystemExit("FLEET_DAYTONA_API_KEY is required")
    receipt = await run_benchmark(settings)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    cycle_counts = cast(dict[str, object], receipt["cycles"])
    print(
        json.dumps(
            {
                "decision": receipt["decision"],
                "measured_completed": cycle_counts["measured_completed"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if receipt["failure"] is None else 1


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_run(argv))


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    raise SystemExit(main())
