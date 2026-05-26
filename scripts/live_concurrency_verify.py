"""Live Daytona Phase 4 concurrency verification lane.

This script exercises the sandbox concurrency semaphore against a live
Daytona environment. It creates sandboxes up to the configured limit,
verifies the next creation times out, cleans up, and reports results.

Requirements:
    - DAYTONA_API_KEY and DAYTONA_API_URL environment variables set
    - FLEET_MAX_CONCURRENT_SANDBOXES set to a low value (e.g., 2) for testing

Usage (from repo root):
    FLEET_MAX_CONCURRENT_SANDBOXES=2 uv run python scripts/live_concurrency_verify.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fleet_rlm.integrations.daytona.concurrency import (
    ConcurrencyConfig,
    get_current_sandbox_usage,
)
from fleet_rlm.integrations.daytona.config import resolve_daytona_config
from fleet_rlm.integrations.daytona.diagnostics import DaytonaDiagnosticError
from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxRuntime


def _print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


async def main() -> int:
    config = ConcurrencyConfig.from_env()
    print(f"Concurrency config: max_sandboxes={config.max_sandboxes}, timeout={config.slot_timeout_seconds}s")

    daytona_config = resolve_daytona_config()
    print(f"Daytona API: {daytona_config.api_url} (target: {daytona_config.target})")

    runtime = DaytonaSandboxRuntime(config=daytona_config)

    sandboxes: list = []
    results: dict[str, str] = {}

    # -----------------------------------------------------------------------
    # Step 1: Create sandboxes up to the limit
    # -----------------------------------------------------------------------
    _print_section(f"Step 1: Create {config.max_sandboxes} sandboxes (fill capacity)")

    for i in range(config.max_sandboxes):
        print(f"  Creating sandbox {i + 1}/{config.max_sandboxes}...", end=" ", flush=True)
        start = time.perf_counter()
        try:
            sandbox = await runtime.acreate_sandbox()
            elapsed = time.perf_counter() - start
            sandboxes.append(sandbox)
            print(f"OK ({elapsed:.1f}s)")
        except Exception as exc:
            elapsed = time.perf_counter() - start
            print(f"FAILED ({elapsed:.1f}s): {exc}")
            results["step_1_fill_capacity"] = f"FAIL at sandbox {i + 1}: {exc}"
            break
    else:
        results["step_1_fill_capacity"] = "PASS"

    usage = get_current_sandbox_usage()
    print(f"\n  Usage: {usage.model_dump()}")

    # -----------------------------------------------------------------------
    # Step 2: Attempt to create one more (should timeout/busy)
    # -----------------------------------------------------------------------
    _print_section("Step 2: Attempt creation beyond limit (expect busy error)")

    print("  Creating sandbox beyond limit...", end=" ", flush=True)
    start = time.perf_counter()
    try:
        extra = await runtime.acreate_sandbox()
        elapsed = time.perf_counter() - start
        sandboxes.append(extra)
        print(f"UNEXPECTED SUCCESS ({elapsed:.1f}s)")
        results["step_2_busy_error"] = "FAIL (no error raised)"
    except DaytonaDiagnosticError as exc:
        elapsed = time.perf_counter() - start
        if exc.category == "sandbox_concurrency_busy":
            print(f"PASS - busy error raised ({elapsed:.1f}s)")
            results["step_2_busy_error"] = "PASS"
        else:
            print(f"FAIL - wrong category: {exc.category} ({elapsed:.1f}s)")
            results["step_2_busy_error"] = f"FAIL (category={exc.category})"
    except Exception as exc:
        elapsed = time.perf_counter() - start
        print(f"FAIL - unexpected error ({elapsed:.1f}s): {exc}")
        results["step_2_busy_error"] = f"FAIL ({type(exc).__name__}: {exc})"

    # -----------------------------------------------------------------------
    # Step 3: Delete one sandbox and verify slot is released
    # -----------------------------------------------------------------------
    _print_section("Step 3: Delete one sandbox, verify slot release")

    if sandboxes:
        print("  Deleting first sandbox...", end=" ", flush=True)
        try:
            sandboxes[0].delete()
            sandboxes.pop(0)
            print("OK")
        except Exception as exc:
            print(f"WARN: {exc}")

        await asyncio.sleep(1.0)
        usage = get_current_sandbox_usage()
        print(f"  Usage after delete: {usage.model_dump()}")

        print("  Creating replacement sandbox...", end=" ", flush=True)
        start = time.perf_counter()
        try:
            replacement = await runtime.acreate_sandbox()
            elapsed = time.perf_counter() - start
            sandboxes.append(replacement)
            print(f"OK ({elapsed:.1f}s)")
            results["step_3_slot_release"] = "PASS"
        except Exception as exc:
            elapsed = time.perf_counter() - start
            print(f"FAIL ({elapsed:.1f}s): {exc}")
            results["step_3_slot_release"] = f"FAIL: {exc}"
    else:
        results["step_3_slot_release"] = "SKIP (no sandboxes created)"

    # -----------------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------------
    _print_section("Cleanup: Delete all remaining sandboxes")

    for i, sb in enumerate(sandboxes):
        print(f"  Deleting sandbox {i + 1}/{len(sandboxes)}...", end=" ", flush=True)
        try:
            sb.delete()
            print("OK")
        except Exception as exc:
            print(f"WARN: {exc}")

    # -----------------------------------------------------------------------
    # Report
    # -----------------------------------------------------------------------
    _print_section("Results")

    all_pass = True
    for step, result in results.items():
        status = "PASS" if result == "PASS" else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  {step}: {result}")

    final_usage = get_current_sandbox_usage()
    print(f"\n  Final usage: {final_usage.model_dump()}")
    print(f"\n  Overall: {'PASS' if all_pass else 'FAIL'}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
