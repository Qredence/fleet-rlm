#!/usr/bin/env python3
"""Quick RLM environment diagnostics.

Run: uv run python .claude/skills/rlm-debug/scripts/diagnose.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def check_modal() -> bool:
    """Check Modal installation and credentials."""
    print("--- Modal ---")
    try:
        import modal

        print(f"  Version: {modal.__version__}")
    except ImportError:
        print("  FAIL: modal not installed. Run: uv sync")
        return False

    token_id = os.environ.get("MODAL_TOKEN_ID", "")
    config_path = Path.home() / ".modal.toml"

    if token_id:
        print(f"  Token (env): {token_id[:8]}...")
    elif config_path.exists():
        print(f"  Token (file): {config_path}")
    else:
        print("  FAIL: No credentials. Run: uv run modal token set")
        return False

    return True


def check_env() -> bool:
    """Check required environment variables."""
    print("\n--- Environment ---")
    env_path = Path(".env")
    if env_path.exists():
        print(f"  .env file: found ({env_path.stat().st_size} bytes)")
    else:
        print("  .env file: MISSING (create at project root)")

    required = ["DSPY_LM_MODEL"]
    fallback_keys = [("DSPY_LLM_API_KEY", "DSPY_LM_API_KEY")]
    ok = True

    for key in required:
        val = os.environ.get(key, "")
        if val:
            print(f"  {key}: {val[:30]}...")
        else:
            print(f"  {key}: MISSING")
            ok = False

    for primary, fallback in fallback_keys:
        val = os.environ.get(primary, "") or os.environ.get(fallback, "")
        if val:
            print(f"  {primary}: {'*' * min(len(val), 8)}...")
        else:
            print(f"  {primary}: MISSING (also checked {fallback})")
            ok = False

    return ok


def check_secret() -> bool:
    """Check LITELLM Modal secret."""
    print("\n--- LITELLM Secret ---")
    try:
        from fleet_rlm.runners import check_secret_presence

        result = check_secret_presence()
        ok = True
        for key, present in result.items():
            status = "OK" if present else "MISSING"
            print(f"  {key}: {status}")
            if not present:
                ok = False
        return ok
    except Exception as e:
        print(f"  Could not check: {e}")
        return False


def check_volumes() -> bool:
    """List Modal volumes."""
    print("\n--- Volumes ---")
    try:
        import subprocess

        result = subprocess.run(
            ["uv", "run", "modal", "volume", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout.strip()
        if output:
            for line in output.splitlines()[:10]:
                print(f"  {line}")
        else:
            print("  (none found)")
        return result.returncode == 0
    except Exception as e:
        print(f"  Could not list: {e}")
        return False


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
        "modal": check_modal(),
        "environment": check_env(),
        "secret": check_secret(),
        "volumes": check_volumes(),
    }

    print("\n" + "=" * 40)
    print("Summary")
    print("=" * 40)
    for name, passed in results.items():
        print(f"  {name:15s}: {'OK' if passed else 'FAIL'}")

    if all(results.values()):
        print("\nAll checks passed.")
    else:
        failed = [k for k, v in results.items() if not v]
        print(f"\nFailed: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
