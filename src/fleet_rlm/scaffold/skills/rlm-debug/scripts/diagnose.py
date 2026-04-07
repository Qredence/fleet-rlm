#!/usr/bin/env python3
"""Quick RLM environment diagnostics.

Run: uv run python .claude/skills/rlm-debug/scripts/diagnose.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def check_daytona() -> bool:
    """Check Daytona configuration and optional CLI availability."""
    print("--- Daytona ---")
    api_key = os.environ.get("DAYTONA_API_KEY", "")
    api_url = os.environ.get("DAYTONA_API_URL", "")

    ok = True
    if api_key:
        print("  DAYTONA_API_KEY: present (hidden)")
    else:
        print("  FAIL: DAYTONA_API_KEY is missing")
        ok = False

    if api_url:
        print(f"  DAYTONA_API_URL: {api_url}")
    else:
        print("  FAIL: DAYTONA_API_URL is missing")
        ok = False

    try:
        result = subprocess.run(
            ["daytona", "version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output = (result.stdout or result.stderr).strip()
        if output:
            print(f"  CLI: {output.splitlines()[0]}")
    except FileNotFoundError:
        print("  CLI: not installed (optional)")

    return ok


def check_env() -> bool:
    """Check required environment variables."""
    print("\n--- Environment ---")
    env_path = Path(".env")
    if env_path.exists():
        print(f"  .env file: found ({env_path.stat().st_size} bytes)")
    else:
        print("  .env file: MISSING (create at project root if needed)")

    required = ["DSPY_LM_MODEL"]
    fallback_keys = [("DSPY_LLM_API_KEY", "DSPY_LM_API_KEY")]
    ok = True

    for key in required:
        val = os.environ.get(key, "")
        if val:
            print(f"  {key}: present ({val})")
        else:
            print(f"  {key}: MISSING")
            ok = False

    for primary, fallback in fallback_keys:
        val = os.environ.get(primary, "") or os.environ.get(fallback, "")
        if val:
            print(f"  {primary}: present (hidden)")
        else:
            print(f"  {primary}: MISSING (also checked {fallback})")
            ok = False

    return ok


def check_daytona_smoke_hint() -> bool:
    """Print the canonical smoke command."""
    print("\n--- Daytona Smoke ---")
    print("  Run: uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]")
    return True


def check_fleet_rlm() -> bool:
    """Check fleet-rlm package."""
    print("\n--- fleet-rlm ---")
    try:
        import fleet_rlm

        version = getattr(fleet_rlm, "__version__", "unknown")
        print(f"  Package: installed (v{version})")
        return True
    except ImportError:
        print("  FAIL: not installed. Run: uv sync")
        return False


def main() -> None:
    """Run all diagnostics."""
    print("=" * 40)
    print("RLM Quick Diagnostics")
    print("=" * 40)
    print(
        f"Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    print(f"CWD: {os.getcwd()}")

    results = {
        "fleet-rlm": check_fleet_rlm(),
        "daytona": check_daytona(),
        "environment": check_env(),
        "smoke": check_daytona_smoke_hint(),
    }

    print("\n" + "=" * 40)
    print("Summary")
    print("=" * 40)
    summary_labels = {
        "fleet-rlm": "fleet-rlm",
        "daytona": "daytona",
        "environment": "environment",
        "smoke": "smoke command",
    }
    for key in ("fleet-rlm", "daytona", "environment", "smoke"):
        passed = results[key]
        label = summary_labels[key]
        print(f"  {label:15s}: {'OK' if passed else 'FAIL'}")

    if all(results.values()):
        print("\nAll checks passed.")
    else:
        failed = [k for k, v in results.items() if not v]
        print(f"\nFailed checks: {len(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
