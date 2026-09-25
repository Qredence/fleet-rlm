#!/usr/bin/env python3
"""Run the repository's AGENTS, documentation, and harness hygiene checks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__:
    from .check_agents_md_freshness import AgentsMdValidator
    from .check_docs_quality import run_checks as run_docs_checks
    from .check_harness_engineering import LOCAL_COMMAND_PATTERN, HarnessChecker
else:  # Direct ``python scripts/check_repo_hygiene.py`` invocation.
    from check_agents_md_freshness import AgentsMdValidator
    from check_docs_quality import run_checks as run_docs_checks
    from check_harness_engineering import LOCAL_COMMAND_PATTERN, HarnessChecker

_REMOVED_COMMANDS = (
    "scripts/live_phase5_verify.py",
    "scripts/live_phase2_recursive_verify.py",
    "scripts/live_p27_snapshot_verify.py",
    "scripts/benchmarks/run_phase4_campaign.py",
    "scripts/verify_child_sandboxes_turn.py",
)
_ACTIVE_DOC_EXCLUSIONS = (Path("internal/history"), Path("internal/legacy-backend"))


def _active_control_files(repo_root: Path) -> list[Path]:
    paths = [
        repo_root / relative
        for relative in (
            "README.md",
            "AGENTS.md",
            "ARCHITECTURE.md",
            "CONTRIBUTING.md",
            "Makefile",
            "scripts/README.md",
        )
    ]
    docs_root = repo_root / "docs"
    if docs_root.is_dir():
        for path in docs_root.rglob("*.md"):
            relative = path.relative_to(docs_root)
            if any(relative.is_relative_to(prefix) for prefix in _ACTIVE_DOC_EXCLUSIONS):
                continue
            if relative.parts[:1] == ("testing",) and "ledger" in path.name:
                continue
            paths.append(path)
    circleci = repo_root / ".circleci"
    if circleci.is_dir():
        paths.extend(path for path in circleci.rglob("*") if path.is_file() and path.suffix in {".yml", ".yaml"})
    return sorted({path for path in paths if path.is_file()})


def check_active_script_references(repo_root: Path) -> list[str]:
    """Require every script path in active guidance and workflows to resolve."""
    errors: list[str] = []
    for path in _active_control_files(repo_root):
        content = path.read_text(encoding="utf-8", errors="ignore")
        relative = path.relative_to(repo_root).as_posix()
        for script_path in LOCAL_COMMAND_PATTERN.findall(content):
            if not (repo_root / script_path).is_file():
                errors.append(f"{relative}: references missing script {script_path}")
    return errors


def check_retired_commands_absent(repo_root: Path) -> list[str]:
    """Keep retired commands out of active operator guidance and automation."""
    errors: list[str] = []
    for path in _active_control_files(repo_root):
        content = path.read_text(encoding="utf-8", errors="ignore")
        relative = path.relative_to(repo_root).as_posix()
        for command in _REMOVED_COMMANDS:
            if command in content:
                errors.append(f"{relative}: retired command remains active: {command}")
    return errors


def run_checks(
    repo_root: Path,
    *,
    check_script_help: bool = True,
    editorial: bool = False,
) -> list[str]:
    """Run all formerly separate CI checks once and return unified diagnostics."""
    errors: list[str] = []
    for error in AgentsMdValidator(repo_root).validate_all():
        errors.append(f"AGENTS [{error.file}] {error.issue}: {error.detail}")
    errors.extend(f"docs {error}" for error in run_docs_checks(repo_root))
    for error in HarnessChecker(
        repo_root,
        check_script_help=check_script_help,
        editorial=editorial,
    ).run():
        errors.append(f"harness [{error.path}]: {error.detail}")
    errors.extend(check_active_script_references(repo_root))
    errors.extend(check_retired_commands_absent(repo_root))
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root to validate.",
    )
    parser.add_argument("--skip-script-help", action="store_true", help="Skip safe executable --help checks.")
    parser.add_argument("--editorial", action="store_true", help="Include local editorial harness checks.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = run_checks(
        args.repo_root.resolve(),
        check_script_help=not args.skip_script_help,
        editorial=args.editorial,
    )
    if errors:
        print("Repository hygiene checks failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("OK: repository hygiene checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
