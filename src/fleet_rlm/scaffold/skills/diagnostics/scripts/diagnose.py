#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path


def print_status(label: str, state: str) -> None:
    print(f"  {label:18s}: {state}")


def secret_status(label: str, value: str) -> bool:
    print_status(label, "REDACTED")
    return bool(value)


def env_presence_status(label: str, value: str) -> bool:
    print_status(label, "CHECKED")
    return bool(value)


def check_fleet_rlm() -> bool:
    print("\n--- fleet-rlm ---")
    if find_spec("fleet_rlm") is None:
        print_status("package", "FAIL")
        return False
    print_status("package", "OK")
    return True


def check_daytona() -> bool:
    print("\n--- Daytona ---")
    api_key = secret_status("DAYTONA_API_KEY", os.environ.get("DAYTONA_API_KEY", ""))
    api_url_value = os.environ.get("DAYTONA_API_URL", "")
    api_url = env_presence_status("DAYTONA_API_URL", api_url_value)
    try:
        subprocess.run(
            ["daytona", "version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        print_status("daytona cli", "OK")
    except subprocess.TimeoutExpired:
        print_status("daytona cli", "FAIL")
    except FileNotFoundError:
        print_status("daytona cli", "OK")
    return api_key and api_url


def check_env() -> bool:
    print("\n--- Environment ---")
    env_path = Path(".env")
    env_ok = env_path.exists()
    print_status(".env file", "OK" if env_ok else "FAIL")
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
    results = [passed for _name, passed in checks]
    if all(results):
        print("\nAll checks passed.")
        return
    print("\nOne or more checks failed.")
    sys.exit(1)


if __name__ == "__main__":
    main()
