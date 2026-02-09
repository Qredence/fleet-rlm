#!/usr/bin/env python3
"""RLM Environment Validation Script

Run this in your terminal to validate your Modal + RLM setup:
    uv run python scripts/validate_rlm_env.py

Uses Modal credentials from ~/.modal.toml
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


# Load Modal credentials from ~/.modal.toml BEFORE importing modal
def load_modal_config():
    """Load Modal token from config file with profile support."""
    config_path = Path.home() / ".modal.toml"
    if config_path.exists():
        try:
            import tomllib

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


def check_modal_credentials() -> bool:
    """Check if Modal credentials are configured."""
    token_id = os.environ.get("MODAL_TOKEN_ID")
    token_secret = os.environ.get("MODAL_TOKEN_SECRET")

    if token_id and token_secret:
        print(f"  MODAL_TOKEN_ID: ✓ ({token_id[:8]}...)")
        print("  MODAL_TOKEN_SECRET: ✓ (hidden)")
        return True
    else:
        print("  ✗ Modal credentials not found in environment")
        print("    Run: modal token set")
        return False


def check_litellm_secret() -> dict[str, bool]:
    """Check LITELLM secret in Modal."""
    print("\n📋 Checking LITELLM Secret...")

    try:
        from fleet_rlm.runners import check_secret_presence

        result = check_secret_presence()

        total = len(result)
        present_count = sum(1 for present in result.values() if present)
        missing_count = total - present_count
        print(f"  Secrets present: {present_count}/{total}")
        if missing_count:
            print(f"  Secrets missing: {missing_count}")

        return result

    except Exception as e:
        print(f"  ✗ Error checking secrets: {e}")
        return {}


def test_sandbox_creation() -> bool:
    """Test basic sandbox creation."""
    print("\n🧪 Testing Sandbox Creation...")

    try:
        from fleet_rlm import ModalInterpreter

        print("  Creating ModalInterpreter...")
        interpreter = ModalInterpreter(timeout=60)

        print("  Starting sandbox...")
        interpreter.start()

        print("  Testing basic execution...")
        result = interpreter.execute("""
import sys
print(f"Python: {sys.version_info[:2]}")
SUBMIT(status="healthy", platform=sys.platform)
""")

        # result is a FinalOutput object with output dict
        output = getattr(result, "output", result)
        if isinstance(output, dict) and output.get("status") == "healthy":
            print(
                f"  ✓ Sandbox healthy (platform: {output.get('platform', 'unknown')})"
            )
            interpreter.shutdown()
            return True
        else:
            print(f"  ✗ Unexpected result: {result} (type: {type(result).__name__})")
            interpreter.shutdown()
            return False

    except Exception as e:
        print(f"  ✗ Sandbox test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_variable_space() -> bool:
    """Test variable persistence."""
    print("\n💾 Testing Variable Space...")

    try:
        from fleet_rlm import ModalInterpreter

        interpreter = ModalInterpreter(timeout=60)
        interpreter.start()

        # Set variable
        interpreter.execute("test_var = {'value': 42, 'items': [1, 2, 3]}")
        print("  ✓ Variable set")

        # Retrieve and modify
        result = interpreter.execute("test_var['items'].append(4)\nSUBMIT(test_var)")

        # result is a FinalOutput with nested output dict from SUBMIT
        final_output = getattr(result, "output", result)
        # The SUBMIT data is nested under 'output' key
        submit_data = (
            final_output.get("output", final_output)
            if isinstance(final_output, dict)
            else final_output
        )
        if (
            isinstance(submit_data, dict)
            and submit_data.get("value") == 42
            and submit_data.get("items") == [1, 2, 3, 4]
        ):
            print("  ✓ Variable persistence working")
            interpreter.shutdown()
            return True
        else:
            print(f"  ✗ Variable mismatch: {result}")
            interpreter.shutdown()
            return False

    except Exception as e:
        print(f"  ✗ Variable space test failed: {e}")
        return False


def test_volume_support() -> bool:
    """Test volume support if available."""
    print("\n💿 Testing Volume Support...")

    try:
        from fleet_rlm import ModalInterpreter

        interpreter = ModalInterpreter(timeout=60, volume_name="rlm-validation-volume")
        interpreter.start()

        # Write to volume
        interpreter.execute("""
with open('/data/validation_test.txt', 'w') as f:
    f.write('volume persistence works')
""")
        print("  ✓ Write to volume successful")

        # Read from volume
        result = interpreter.execute("""
with open('/data/validation_test.txt', 'r') as f:
    content = f.read()
SUBMIT(content)
""")

        # result is a FinalOutput - data is in the 'output' attribute
        final_output = getattr(result, "output", result)
        # SUBMIT(content) puts the value under the 'output' key
        content = final_output.get("output") if isinstance(final_output, dict) else None
        if content == "volume persistence works":
            print("  ✓ Volume read/write working")
            interpreter.shutdown()
            return True
        else:
            print("  ✗ Volume content mismatch")
            interpreter.shutdown()
            return False

    except Exception as e:
        print(f"  ✗ Volume test failed: {e}")
        return False


def test_dspy_rlm() -> bool:
    """Test full dspy.RLM integration."""
    print("\n🤖 Testing dspy.RLM Integration...")

    try:
        import dspy
        from fleet_rlm import ModalInterpreter, configure_planner_from_env

        # Configure the LM from environment
        configure_planner_from_env()

        interpreter = ModalInterpreter(timeout=120)

        rlm = dspy.RLM(
            signature="question -> answer",
            interpreter=interpreter,
            max_iterations=5,
            max_llm_calls=10,
            verbose=False,
        )

        result = rlm(question="What is 2 + 2? Calculate using Python.")

        trajectory = getattr(result, "trajectory", [])
        print(f"  ✓ RLM completed ({len(trajectory)} iterations)")
        print(f"  Answer: {getattr(result, 'answer', 'N/A')[:100]}...")

        interpreter.shutdown()
        return True

    except Exception as e:
        print(f"  ✗ dspy.RLM test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Run full validation suite."""
    print("=" * 60)
    print("RLM Environment Validation")
    print("=" * 60)
    print(f"\nPython: {sys.version}")
    print(f"Working directory: {os.getcwd()}")

    results = {
        "modal_credentials": False,
        "litellm_secret": False,
        "sandbox_creation": False,
        "variable_space": False,
        "volume_support": False,
        "dspy_rlm": False,
    }

    # 1. Modal Credentials
    print("\n" + "=" * 60)
    print("🔑 Checking Modal Credentials...")
    results["modal_credentials"] = check_modal_credentials()

    if not results["modal_credentials"]:
        print("\n❌ Validation failed: Modal credentials required")
        sys.exit(1)

    # 2. LITELLM Secret
    litellm_result = check_litellm_secret()
    results["litellm_secret"] = (
        all(litellm_result.values()) if litellm_result else False
    )

    if not results["litellm_secret"]:
        print("\n⚠️  LITELLM secret incomplete - some tests may fail")
        print(
            "    Run: modal secret create LITELLM DSPY_LM_MODEL=... DSPY_LLM_API_KEY=..."
        )

    # 3. Sandbox Creation
    results["sandbox_creation"] = test_sandbox_creation()

    if not results["sandbox_creation"]:
        print("\n❌ Validation failed: Cannot create Modal sandbox")
        sys.exit(1)

    # 4. Variable Space
    results["variable_space"] = test_variable_space()

    # 5. Volume Support
    results["volume_support"] = test_volume_support()

    # 6. dspy.RLM Integration
    if results["litellm_secret"]:
        results["dspy_rlm"] = test_dspy_rlm()
    else:
        print("\n⏭️  Skipping dspy.RLM test (LITELLM secret not configured)")

    # Summary
    print("\n" + "=" * 60)
    print("📊 Validation Summary")
    print("=" * 60)

    for test, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {test:20s}: {status}")

    all_critical = all(
        [
            results["modal_credentials"],
            results["sandbox_creation"],
            results["variable_space"],
        ]
    )

    print("\n" + "=" * 60)
    if all_critical and results["dspy_rlm"]:
        print("✅ All tests passed! Environment ready for RLM.")
        sys.exit(0)
    elif all_critical:
        print("⚠️  Basic tests passed. Configure LITELLM for full RLM functionality.")
        sys.exit(0)
    else:
        print("❌ Validation failed. Check errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
