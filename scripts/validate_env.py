#!/usr/bin/env python3
"""Unified CLI for environment and agent validations."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

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


def _validate_agent(path: Path) -> list[str]:
    errors: list[str] = []
    content = path.read_text(encoding="utf-8")

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


def _print_masked(key: str, value: str | None) -> None:
    if value:
        print(f"  {key}: ✓ ({value[:8]}...)")
    else:
        print(f"  {key}: ✗ missing")


def _check_daytona_config() -> bool:
    print("\nChecking Daytona configuration...")
    try:
        from fleet_rlm.integrations.daytona import resolve_daytona_config

        config = resolve_daytona_config()
    except Exception as exc:
        print(f"  ✗ {exc}")
        return False

    _print_masked("DAYTONA_API_KEY", config.api_key)
    print(f"  DAYTONA_API_URL: ✓ ({config.api_url})")
    if config.target:
        print(f"  DAYTONA_TARGET: ✓ ({config.target})")
    return True


def _check_lm_runtime_config() -> bool:
    print("\nChecking sandbox-local LM configuration...")
    try:
        from fleet_rlm.integrations.daytona import resolve_daytona_lm_runtime_config

        config = resolve_daytona_lm_runtime_config()
    except Exception as exc:
        print(f"  ✗ {exc}")
        return False

    print(f"  DSPY_LM_MODEL: ✓ ({config.model})")
    _print_masked("DSPY_LLM_API_KEY", config.api_key)
    if config.api_base:
        print(f"  DSPY_LM_API_BASE: ✓ ({config.api_base})")
    return True


def _test_fleet_rlm_import() -> bool:
    print("\nTesting fleet_rlm import...")
    try:
        from fleet_rlm import DaytonaInterpreter  # noqa: F401

        print("  ✓ DaytonaInterpreter imported")
        return True
    except ImportError as exc:
        print(f"  ✗ Cannot import fleet_rlm: {exc}")
        return False


def _run_daytona_smoke(repo: str, ref: str | None, timeout: int) -> bool:
    print("\nRunning Daytona smoke validation...")
    try:
        from fleet_rlm.integrations.daytona import run_daytona_smoke

        result = run_daytona_smoke(repo=repo, ref=ref, timeout=float(timeout))
    except Exception as exc:
        print(f"  ✗ Smoke validation crashed: {exc}")
        return False

    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return result.error_category is None


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


def do_daytona(args: argparse.Namespace) -> int:
    print("=" * 60)
    print("Fleet RLM Daytona Environment Validation")
    print("=" * 60)
    print(f"\nPython: {sys.version}")
    print(f"Working directory: {os.getcwd()}")

    daytona_ok = _check_daytona_config()
    import_ok = _test_fleet_rlm_import()
    lm_ok = _check_lm_runtime_config()

    smoke_ok = True
    if args.skip_smoke:
        print("\nSkipping Daytona smoke validation (--skip-smoke)")
    elif not args.repo:
        print("\nSkipping Daytona smoke validation (no --repo provided)")
    elif not daytona_ok or not import_ok:
        print("\nSkipping Daytona smoke validation (config/import checks failed)")
        smoke_ok = False
    else:
        smoke_ok = _run_daytona_smoke(args.repo, args.ref, args.timeout)

    print("\n" + "=" * 60)
    print("Validation Summary")
    print("=" * 60)
    summary = {
        "daytona_config": daytona_ok,
        "fleet_rlm_import": import_ok,
        "lm_runtime_config": lm_ok,
    }
    if not args.skip_smoke and args.repo:
        summary["daytona_smoke"] = smoke_ok

    for name, passed in summary.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {name:20s}: {status}")

    if not daytona_ok or not import_ok:
        print("\nValidation failed: Daytona config and fleet_rlm import are required.")
        return 1
    if args.require_lm_config and not lm_ok:
        print("\nValidation failed: sandbox-local LM config is required.")
        return 1
    if "daytona_smoke" in summary and not smoke_ok:
        print("\nValidation failed: Daytona smoke validation did not complete cleanly.")
        return 1

    if not lm_ok:
        print(
            "\nBasic Daytona checks passed. Configure DSPY_LM_MODEL and "
            "DSPY_LLM_API_KEY / DSPY_LM_API_KEY for self-orchestrated runs."
        )
    else:
        print("\nAll requested Daytona checks passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fleet RLM environment and agent validation"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    parser_agents = subparsers.add_parser(
        "agents", help="Validate .claude/agents/*.md files"
    )
    parser_agents.set_defaults(func=do_agents)

    parser_daytona = subparsers.add_parser(
        "daytona",
        help="Run Daytona and fleet_rlm environment diagnostics",
    )
    parser_daytona.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Timeout in seconds for Daytona smoke validation.",
    )
    parser_daytona.add_argument(
        "--repo",
        help="Repository URL to clone for Daytona smoke validation.",
    )
    parser_daytona.add_argument(
        "--ref",
        help="Optional branch or commit SHA for Daytona smoke validation.",
    )
    parser_daytona.add_argument(
        "--require-lm-config",
        action="store_true",
        help="Fail when sandbox-local LM configuration is missing.",
    )
    parser_daytona.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Skip the live Daytona smoke validation step.",
    )
    parser_daytona.set_defaults(func=do_daytona)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
