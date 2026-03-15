#!/usr/bin/env python3
"""Unified CLI for environment and agent validations."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import yaml

VALID_TOOLS = {"Read", "Write", "Edit", "Bash", "Grep", "Glob", "Task"}
VALID_MODELS = {"sonnet", "opus", "haiku", "inherit"}
VALID_PERM_MODES = {
    "default",
    "acceptEdits",
    "delegate",
    "dontAsk",
    "bypassPermissions",
    "plan",
}
REQUIRED_FIELDS = {"name", "description"}
OPTIONAL_FIELDS = {
    "tools",
    "disallowedTools",
    "model",
    "permissionMode",
    "maxTurns",
    "skills",
    "mcpServers",
    "hooks",
    "memory",
}
ALL_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS


def _load_modal_config() -> bool:
    """Load Modal credentials from ~/.modal.toml, if available."""
    config_path = Path.home() / ".modal.toml"
    if not config_path.exists():
        return False

    try:
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover - py310 fallback
            import tomli as tomllib  # type: ignore[no-redef]

        with config_path.open("rb") as f:
            config = tomllib.load(f)

        active_profile = None
        for profile_data in config.values():
            if isinstance(profile_data, dict) and profile_data.get("active"):
                active_profile = profile_data
                break

        if active_profile is None:
            for profile_data in config.values():
                if isinstance(profile_data, dict) and "token_id" in profile_data:
                    active_profile = profile_data
                    break

        if active_profile is None:
            return False

        token_id = active_profile.get("token_id")
        token_secret = active_profile.get("token_secret")
        if token_id and token_secret:
            os.environ["MODAL_TOKEN_ID"] = token_id
            os.environ["MODAL_TOKEN_SECRET"] = token_secret
            print(f"Loaded Modal credentials from {config_path}")
            return True
    except Exception as exc:
        print(f"Warning: Error loading ~/.modal.toml: {exc}")

    return False


def _validate_agent(path: Path) -> list[str]:
    errors: list[str] = []
    content = path.read_text()

    if not content.startswith("---"):
        return [f"{path.name}: Missing YAML frontmatter"]

    parts = content.split("---", 2)
    if len(parts) < 3:
        return [f"{path.name}: Malformed frontmatter"]

    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        return [f"{path.name}: YAML parse error: {exc}"]

    if not isinstance(fm, dict):
        return [f"{path.name}: Frontmatter is not a mapping"]

    for field in REQUIRED_FIELDS:
        if field not in fm:
            errors.append(f'{path.name}: Missing required field "{field}"')

    for key in fm:
        if key not in ALL_FIELDS:
            errors.append(f'{path.name}: Unknown frontmatter field "{key}"')

    if "model" in fm and fm["model"] not in VALID_MODELS:
        errors.append(f'{path.name}: Invalid model "{fm["model"]}"')

    if "tools" in fm:
        tools_raw = fm["tools"]
        if isinstance(tools_raw, str):
            tools_list = [tool.strip() for tool in tools_raw.split(",")]
        elif isinstance(tools_raw, list):
            tools_list = [str(tool) for tool in tools_raw]
        else:
            tools_list = []
            errors.append(f"{path.name}: tools must be string or list")

        for tool in tools_list:
            base = tool.split("(")[0].strip()
            if base not in VALID_TOOLS:
                errors.append(f'{path.name}: Unknown tool "{tool}" (base: "{base}")')

    if "permissionMode" in fm and fm["permissionMode"] not in VALID_PERM_MODES:
        errors.append(f'{path.name}: Invalid permissionMode "{fm["permissionMode"]}"')

    if "memory" in fm and fm["memory"] not in {"user", "project", "local"}:
        errors.append(f'{path.name}: Invalid memory scope "{fm["memory"]}"')

    body = parts[2]
    line_count = len(body.strip().splitlines())

    status = "✗" if errors else "✓"
    print(f"{status} {path.name}")
    print(f"  name: {fm.get('name', 'MISSING')}")
    print(f"  model: {fm.get('model', '(not set - inherits)')}")
    print(f"  tools: {fm.get('tools', '(inherits all)')}")
    print(f"  skills: {fm.get('skills', '(none)')}")
    print(f"  maxTurns: {fm.get('maxTurns', '(not set)')}")
    print(f"  memory: {fm.get('memory', '(not set)')}")
    print(f"  body lines: {line_count}")
    print()

    return errors


def _check_modal_import() -> bool:
    print("\nTesting Modal import...")
    try:
        import modal

        print(f"  ✓ Modal version: {modal.__version__}")
        return True
    except ImportError as exc:
        print(f"  ✗ Cannot import Modal: {exc}")
        return False


def _check_modal_credentials() -> bool:
    print("\n🔑 Checking Modal Credentials...")
    token_id = os.environ.get("MODAL_TOKEN_ID")
    token_secret = os.environ.get("MODAL_TOKEN_SECRET")

    if token_id and token_secret:
        print(f"  MODAL_TOKEN_ID: ✓ ({token_id[:8]}...)")
        print("  MODAL_TOKEN_SECRET: ✓ (hidden)")
        return True

    print("  ✗ Modal credentials not found in environment")
    print("    Run: modal token set")
    return False


def _check_modal_app_lookup() -> bool:
    print("\nTesting Modal credentials...")
    try:
        import modal

        app = modal.App.lookup("test-connection", create_if_missing=True)
        print(f"  ✓ Credentials working (app: {app.name})")
        return True
    except Exception as exc:
        print(f"  ✗ Credentials failed: {exc}")
        return False


def _check_litellm_secret(secret_name: str) -> dict[str, bool]:
    print("\n📋 Checking LITELLM Secret...")
    try:
        from fleet_rlm.runners import check_secret_presence

        result = check_secret_presence(secret_name=secret_name)
        total = len(result)
        present_count = sum(1 for present in result.values() if present)
        missing_count = total - present_count
        print(f"  Secrets present: {present_count}/{total}")
        if missing_count:
            print(f"  Secrets missing: {missing_count}")
        return result
    except Exception as exc:
        print(f"  ✗ Error checking secrets: {exc}")
        return {}


def _test_fleet_rlm_import() -> bool:
    print("\nTesting fleet_rlm import...")
    try:
        from fleet_rlm import ModalInterpreter  # noqa: F401

        print("  ✓ ModalInterpreter imported")
        return True
    except ImportError as exc:
        print(f"  ✗ Cannot import fleet_rlm: {exc}")
        return False


def _test_sandbox_creation(timeout: int) -> bool:
    print("\n🧪 Testing Sandbox Creation...")
    try:
        from fleet_rlm import ModalInterpreter

        interpreter = ModalInterpreter(timeout=timeout)
        interpreter.start()
        try:
            result = interpreter.execute(
                """
import sys
print(f"Python: {sys.version_info[:2]}")
SUBMIT(status="healthy", platform=sys.platform)
"""
            )
            output = getattr(result, "output", result)
            if isinstance(output, dict) and output.get("status") == "healthy":
                print(
                    "  ✓ Sandbox healthy "
                    f"(platform: {output.get('platform', 'unknown')})"
                )
                return True
            print(f"  ✗ Unexpected result: {result} (type: {type(result).__name__})")
            return False
        finally:
            interpreter.shutdown()
    except Exception as exc:
        print(f"  ✗ Sandbox test failed: {exc}")
        return False


def _test_variable_space(timeout: int) -> bool:
    print("\n💾 Testing Variable Space...")
    try:
        from fleet_rlm import ModalInterpreter

        interpreter = ModalInterpreter(timeout=timeout)
        interpreter.start()
        try:
            interpreter.execute("test_var = {'value': 42, 'items': [1, 2, 3]}")
            print("  ✓ Variable set")

            result = interpreter.execute(
                "test_var['items'].append(4)\nSUBMIT(test_var)"
            )
            final_output = getattr(result, "output", result)
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
                return True
            print(f"  ✗ Variable mismatch: {result}")
            return False
        finally:
            interpreter.shutdown()
    except Exception as exc:
        print(f"  ✗ Variable space test failed: {exc}")
        return False


def _test_volume_support(timeout: int, volume_name: str) -> bool:
    print("\n💿 Testing Volume Support...")
    try:
        from fleet_rlm import ModalInterpreter

        interpreter = ModalInterpreter(timeout=timeout, volume_name=volume_name)
        interpreter.start()
        try:
            interpreter.execute(
                """
with open('/data/validation_test.txt', 'w') as f:
    f.write('volume persistence works')
"""
            )
            print("  ✓ Write to volume successful")

            result = interpreter.execute(
                """
with open('/data/validation_test.txt', 'r') as f:
    content = f.read()
SUBMIT(content)
"""
            )
            final_output = getattr(result, "output", result)
            content = (
                final_output.get("output") if isinstance(final_output, dict) else None
            )
            if content == "volume persistence works":
                print("  ✓ Volume read/write working")
                return True
            print("  ✗ Volume content mismatch")
            return False
        finally:
            interpreter.shutdown()
    except Exception as exc:
        print(f"  ✗ Volume test failed: {exc}")
        return False


def _test_dspy_rlm(timeout: int) -> bool:
    print("\n🤖 Testing dspy.RLM Integration...")
    try:
        import dspy
        from fleet_rlm import ModalInterpreter, configure_planner_from_env

        configure_planner_from_env()
        interpreter = ModalInterpreter(timeout=timeout)
        try:
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
            return True
        finally:
            interpreter.shutdown()
    except Exception as exc:
        print(f"  ✗ dspy.RLM test failed: {exc}")
        return False


def do_agents(args: argparse.Namespace) -> int:
    _ = args
    agents_dir = Path(".claude/agents")
    if not agents_dir.exists():
        print("No .claude/agents/ directory found")
        return 1

    all_errors: list[str] = []
    for path in sorted(agents_dir.glob("*.md")):
        all_errors.extend(_validate_agent(path))

    if all_errors:
        print("ERRORS:")
        for error in all_errors:
            print(f"  ✗ {error}")
        return 1

    print("All agents pass validation! ✓")
    return 0


def do_modal(args: argparse.Namespace) -> int:
    if not _load_modal_config():
        print("Warning: Could not load ~/.modal.toml, using existing environment")

    print("=" * 60)
    print("RLM Environment Validation")
    print("=" * 60)
    print(f"\nPython: {sys.version}")
    print(f"Working directory: {os.getcwd()}")

    results = {
        "modal_import": _check_modal_import(),
        "modal_credentials": _check_modal_credentials(),
    }

    if not results["modal_import"] or not results["modal_credentials"]:
        print("\n❌ Validation failed: Modal import and credentials are required")
        return 1

    results["modal_app_lookup"] = _check_modal_app_lookup()
    results["fleet_rlm_import"] = _test_fleet_rlm_import()

    if not results["modal_app_lookup"] or not results["fleet_rlm_import"]:
        print("\n❌ Validation failed: Modal connectivity or fleet_rlm import failed")
        return 1

    litellm_result = _check_litellm_secret(args.secret_name)
    results["litellm_secret"] = (
        all(litellm_result.values()) if litellm_result else False
    )
    if not results["litellm_secret"]:
        print("\n⚠️  LITELLM secret incomplete - some tests may fail")
        print(
            "    Run: modal secret create "
            f"{args.secret_name} DSPY_LM_MODEL=... DSPY_LLM_API_KEY=..."
        )

    results["sandbox_creation"] = _test_sandbox_creation(args.timeout)
    if not results["sandbox_creation"]:
        print("\n❌ Validation failed: Cannot create Modal sandbox")
        return 1

    results["variable_space"] = _test_variable_space(args.timeout)
    results["volume_support"] = _test_volume_support(
        args.timeout,
        args.volume_name,
    )

    if args.skip_dspy_rlm:
        print("\n⏭️  Skipping dspy.RLM test (--skip-dspy-rlm)")
        results["dspy_rlm"] = False
    elif results["litellm_secret"]:
        results["dspy_rlm"] = _test_dspy_rlm(args.rlm_timeout)
    else:
        print("\n⏭️  Skipping dspy.RLM test (LITELLM secret not configured)")
        results["dspy_rlm"] = False

    print("\n" + "=" * 60)
    print("📊 Validation Summary")
    print("=" * 60)
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {test_name:20s}: {status}")

    all_critical = all(
        [
            results["modal_import"],
            results["modal_credentials"],
            results["modal_app_lookup"],
            results["fleet_rlm_import"],
            results["sandbox_creation"],
            results["variable_space"],
        ]
    )

    print("\n" + "=" * 60)
    if all_critical and results["dspy_rlm"]:
        print("✅ All tests passed! Environment ready for RLM.")
        return 0
    if all_critical:
        print("⚠️  Basic tests passed. Configure LITELLM for full RLM functionality.")
        return 0

    print("❌ Validation failed. Check errors above.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fleet RLM environment and agent validation"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    parser_agents = subparsers.add_parser(
        "agents", help="Validate .claude/agents/*.md files"
    )
    parser_agents.set_defaults(func=do_agents)

    parser_modal = subparsers.add_parser(
        "modal",
        help="Run Modal, sandbox, and fleet_rlm environment diagnostics",
    )
    parser_modal.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Interpreter timeout for sandbox, variable, and volume tests.",
    )
    parser_modal.add_argument(
        "--rlm-timeout",
        type=int,
        default=120,
        help="Interpreter timeout for the dspy.RLM integration test.",
    )
    parser_modal.add_argument(
        "--secret-name",
        default="LITELLM",
        help="Modal secret name used for LITELLM checks.",
    )
    parser_modal.add_argument(
        "--volume-name",
        default="rlm-validation-volume",
        help="Volume name used for the volume support check.",
    )
    parser_modal.add_argument(
        "--skip-dspy-rlm",
        action="store_true",
        help="Skip the full dspy.RLM integration step.",
    )
    parser_modal.set_defaults(func=do_modal)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
