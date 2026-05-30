#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def status(label: str, ok: bool, detail: str = "") -> bool:
    state = "OK" if ok else "FAIL"
    suffix = f" ({detail})" if detail else ""
    print(f"  {label:18s}: {state}{suffix}")
    return ok


def secret_status(label: str, value: str) -> bool:
    print(f"  {label:18s}: REDACTED")
    return bool(value)


def env_presence_status(label: str, value: str) -> bool:
    print(f"  {label:18s}: CHECKED")
    return bool(value)


def check_fleet_rlm() -> bool:
    print("\n--- fleet-rlm ---")
    try:
        import fleet_rlm
    except ImportError:
        return status("package", False, "run: uv sync")
    version = getattr(fleet_rlm, "__version__", "unknown")
    return status("package", True, f"v{version}")


def check_daytona() -> bool:
    print("\n--- Daytona ---")
    api_key = secret_status("DAYTONA_API_KEY", os.environ.get("DAYTONA_API_KEY", ""))
    api_url_value = os.environ.get("DAYTONA_API_URL", "")
    api_url = env_presence_status("DAYTONA_API_URL", api_url_value)
    try:
        result = subprocess.run(
            ["daytona", "version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output = (result.stdout + result.stderr).strip().splitlines()
        status("daytona cli", True, output[0] if output else "installed")
    except subprocess.TimeoutExpired:
        status("daytona cli", False, "timeout")
    except FileNotFoundError:
        status("daytona cli", True, "optional")
    return api_key and api_url


def check_env() -> bool:
    print("\n--- Environment ---")
    env_path = Path(".env")
    env_ok = status(".env file", env_path.exists(), f"{env_path.stat().st_size} bytes" if env_path.exists() else "")
    model_value = os.environ.get("DSPY_LM_MODEL", "")
    model_ok = env_presence_status("DSPY_LM_MODEL", model_value)
    api_key = os.environ.get("DSPY_LLM_API_KEY", "") or os.environ.get("DSPY_LM_API_KEY", "")
    key_ok = secret_status("DSPY_LLM_API_KEY", api_key)
    return env_ok and model_ok and key_ok


def check_daytona_smoke_hint() -> bool:
    print("\n--- Daytona Smoke ---")
    print("  uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]")
    return True


def main() -> None:
    print("=" * 40)
    print("RLM Quick Diagnostics")
    print("=" * 40)
    print(f"Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    print(f"CWD: {os.getcwd()}")

    checks = (
        ("fleet-rlm", check_fleet_rlm()),
        ("daytona", check_daytona()),
        ("environment", check_env()),
        ("smoke command", check_daytona_smoke_hint()),
    )

    print("\n" + "=" * 40)
    print("Summary")
    print("=" * 40)
    results = [status(name, passed) for name, passed in checks]
    if all(results):
        print("\nAll checks passed.")
        return
    print(f"\nFailed checks: {results.count(False)}")
    sys.exit(1)


if __name__ == "__main__":
    main()
