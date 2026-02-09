#!/usr/bin/env python3
"""Quick Modal connection test.

Run in your terminal:
    uv run python scripts/test_modal_connection.py

Uses Modal credentials from ~/.modal.toml
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


# Load Modal credentials from ~/.modal.toml
def load_modal_config():
    """Load Modal token from config file with profile support."""
    config_path = Path.home() / ".modal.toml"
    if config_path.exists():
        try:
            try:
                import tomllib  # type: ignore
            except ImportError:
                # Python 3.10 doesn't have tomllib in stdlib
                import tomli as tomllib  # type: ignore

            with open(config_path, "rb") as f:
                config = tomllib.load(f)

            # Find active profile
            active_profile = None
            for profile_name, profile_data in config.items():
                if isinstance(profile_data, dict) and profile_data.get("active"):
                    active_profile = profile_data
                    break

            # Fallback to first profile if no active one found
            if not active_profile:
                for profile_name, profile_data in config.items():
                    if isinstance(profile_data, dict) and "token_id" in profile_data:
                        active_profile = profile_data
                        break

            if active_profile:
                token_id = active_profile.get("token_id")
                token_secret = active_profile.get("token_secret")
                if token_id and token_secret:
                    os.environ["MODAL_TOKEN_ID"] = token_id
                    os.environ["MODAL_TOKEN_SECRET"] = token_secret
                    print(f"Loaded Modal credentials from {config_path}")
                    return True
        except Exception as e:
            print(f"Warning: Error loading ~/.modal.toml: {e}")
    return False


if not load_modal_config():
    print("Warning: Could not load ~/.modal.toml, using existing environment")


def test_modal_import():
    """Test that Modal can be imported."""
    print("Testing Modal import...")
    try:
        import modal

        print(f"  ✓ Modal version: {modal.__version__}")
        return True
    except ImportError as e:
        print(f"  ✗ Cannot import Modal: {e}")
        return False


def test_modal_credentials():
    """Test Modal credentials are available."""
    print("\nTesting Modal credentials...")
    try:
        import modal

        # This will fail if credentials aren't configured
        app = modal.App.lookup("test-connection", create_if_missing=True)
        print(f"  ✓ Credentials working (app: {app.name})")
        return True
    except Exception as e:
        print(f"  ✗ Credentials failed: {e}")
        return False


def test_sandbox_creation():
    """Test creating a simple sandbox."""
    print("\nTesting sandbox creation...")
    try:
        import modal

        app = modal.App.lookup("test-connection", create_if_missing=True)

        print("  Creating sandbox (timeout=30s)...")
        sb = modal.Sandbox.create(
            app=app,
            timeout=30,
        )

        print(f"  ✓ Sandbox created (ID: {sb.object_id[:8]}...)")

        # Test simple execution
        print("  Testing exec...")
        proc = sb.exec("python", "-c", "print('Hello from Modal!')")
        proc.wait()

        output = proc.stdout.read().strip()
        print(f"  ✓ Exec working: '{output}'")

        sb.terminate()
        print("  ✓ Sandbox terminated cleanly")
        return True

    except Exception as e:
        print(f"  ✗ Sandbox test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_fleet_rlm_import():
    """Test fleet_rlm can be imported."""
    print("\nTesting fleet_rlm import...")
    try:
        from fleet_rlm import ModalInterpreter  # noqa: F401

        print("  ✓ ModalInterpreter imported")
        return True
    except ImportError as e:
        print(f"  ✗ Cannot import fleet_rlm: {e}")
        return False


def main():
    """Run connection tests."""
    print("=" * 60)
    print("Modal Connection Test")
    print("=" * 60)

    tests = [
        ("Modal Import", test_modal_import),
        ("Modal Credentials", test_modal_credentials),
        ("Sandbox Creation", test_sandbox_creation),
        ("fleet_rlm Import", test_fleet_rlm_import),
    ]

    results = {}
    for name, test_func in tests:
        results[name] = test_func()
        if not results[name]:
            print(f"\n❌ Stopping at {name} - fix this first")
            break

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    for name, passed in results.items():
        status = "✓" if passed else "✗"
        print(f"  {name:25s}: {status}")

    if all(results.values()):
        print("\n✅ All connection tests passed!")
        return 0
    else:
        print("\n❌ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
