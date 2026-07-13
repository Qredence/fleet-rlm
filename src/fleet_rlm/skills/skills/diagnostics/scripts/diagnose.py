#!/usr/bin/env python3
"""Offline-friendly diagnostics for fleet_rlm (no secret values printed)."""

from __future__ import annotations

import os
import sys
from importlib.util import find_spec
from pathlib import Path


def print_status(label: str, state: str) -> None:
    print(f"  {label:22s}: {state}")


def secret_presence(label: str, value: str | None) -> bool:
    print_status(label, "SET" if value else "MISSING")
    return bool(value and value.strip())


def load_dotenv_missing(env_path: Path = Path(".env")) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip("\"'")


def check_package() -> bool:
    print("\n--- package ---")
    ok = find_spec("fleet_rlm") is not None
    print_status("fleet_rlm", "OK" if ok else "FAIL")
    return ok


def check_env() -> bool:
    print("\n--- environment (presence only) ---")
    load_dotenv_missing()
    env_ok = Path(".env").exists()
    print_status(".env file", "OK" if env_ok else "MISSING (optional)")

    auth = (os.environ.get("FLEET_AUTH_MODE") or "dev").strip().lower()
    auth_ok = auth in {"dev", "neon"}
    print_status("FLEET_AUTH_MODE", auth if auth_ok else f"INVALID ({auth!r})")

    llm_key = os.environ.get("FLEET_LLM_API_KEY", "")
    daytona_key = os.environ.get("FLEET_DAYTONA_API_KEY", "")
    # Presence is informational for offline; live needs these set.
    secret_presence("FLEET_LLM_API_KEY", llm_key)
    secret_presence("FLEET_DAYTONA_API_KEY", daytona_key)

    environment = (os.environ.get("FLEET_RUN_ENVIRONMENT") or "hermetic").strip().lower()
    print_status("FLEET_RUN_ENVIRONMENT", environment)

    mount = os.environ.get("FLEET_VOLUME_MOUNT_PATH") or "/home/daytona/fleet"
    print_status("VOLUME_MOUNT_PATH", mount)

    return auth_ok


def check_settings_import() -> bool:
    print("\n--- settings ---")
    try:
        from fleet_rlm.config import Settings

        settings = Settings()
        print_status("Settings()", "OK")
        print_status("auth_mode", settings.auth_mode)
        print_status("run_environment", settings.run_environment)
        return True
    except Exception as exc:  # noqa: BLE001 - operator script
        print_status("Settings()", f"FAIL ({type(exc).__name__})")
        return False


def main() -> None:
    print("=" * 40)
    print("fleet_rlm diagnostics")
    print("=" * 40)
    print(f"Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    print(f"CWD: {os.getcwd()}")

    checks = (
        ("package", check_package()),
        ("environment", check_env()),
        ("settings", check_settings_import()),
    )

    print("\n" + "=" * 40)
    print("Summary")
    print("=" * 40)
    if all(passed for _name, passed in checks):
        print("\nRequired checks passed (live keys may still be missing).")
        return
    print("\nOne or more required checks failed.")
    sys.exit(1)


if __name__ == "__main__":
    main()
