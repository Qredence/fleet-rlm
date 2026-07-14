#!/usr/bin/env python3
"""Quality checks for active documentation under docs/."""

from __future__ import annotations

import argparse
import functools
import re
import subprocess
import sys
from pathlib import Path

LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
OPENAPI_PATH_PATTERN = re.compile(r"^\s{2}/", re.MULTILINE)

EXTERNAL_PREFIXES = (
    "http://",
    "https://",
    "mailto:",
    "#",
    "discussion://",
    "collection://",
)

LEGACY_DOC_DIRS = ("artifacts", "plans", "references", "reviews")
ARCHIVED_DOC_PREFIXES = (Path("internal/history"), Path("internal/legacy-backend"))
LEGACY_EXPLANATION_MARKERS = (
    Path("explanation/README.md"),
    Path("explanation/architecture.md"),
    Path("explanation/rlm-concepts.md"),
    Path("explanation/stateful-architecture.md"),
    Path("explanation/memory-topology.md"),
    Path("explanation/memory-topology"),
)

CLI_CONTRACT_COMMANDS = (("uv", "run", "fleet-rlm", "--help"),)

CANONICAL_RUN_ENVIRONMENTS = frozenset({"hermetic", "deno", "daytona"})
CANONICAL_ENVIRONMENT_DOCS = (
    Path("docs/adr/0002-canonical-deno-and-ink-terminal.md"),
    Path("PRODUCT.md"),
    Path("docs/architecture.md"),
    Path("docs/reference/database.md"),
)
CANONICAL_ENVIRONMENT_DECLARATION = re.compile(
    r"^Canonical Run Environment set:\s*(?P<values>[^\n]+)$",
    re.MULTILINE,
)
INLINE_CODE_VALUE = re.compile(r"`([a-z][a-z0-9_-]*)`")


def iter_docs_files(docs_root: Path) -> list[Path]:
    """Return every tracked Markdown document, including any future internal docs."""
    repo_root = docs_root.parent
    result = subprocess.run(
        ("git", "ls-files", "docs"),
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return sorted(
            repo_root / rel_path
            for rel_path in result.stdout.splitlines()
            if rel_path.endswith(".md") and (repo_root / rel_path).is_file()
        )
    return sorted(path for path in docs_root.rglob("*.md") if path.is_file())


@functools.lru_cache(maxsize=None)
def _resolve_path(parent_path: Path, target: str) -> Path:
    return (parent_path / target).resolve()


def _local_targets(file_path: Path, text: str) -> list[tuple[str, Path]]:
    links: list[tuple[str, Path]] = []
    for raw_target in LINK_PATTERN.findall(text):
        if not raw_target or raw_target.startswith(EXTERNAL_PREFIXES):
            continue
        clean = raw_target.split("#", 1)[0]
        if not clean:
            continue
        resolved = _resolve_path(file_path.parent, clean)
        links.append((raw_target, resolved))
    return links


def check_internal_links(docs_root: Path, files: list[Path]) -> list[str]:
    errors: list[str] = []
    for file_path in files:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        for raw_target, resolved in _local_targets(file_path, text):
            if not resolved.exists():
                rel_file = file_path.relative_to(docs_root.parent).as_posix()
                errors.append(f"broken link: {rel_file} -> {raw_target}")
    return errors


def check_banned_link_schemes(docs_root: Path, files: list[Path]) -> list[str]:
    errors: list[str] = []
    banned = "file://"
    for file_path in files:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        if banned in text:
            rel_file = file_path.relative_to(docs_root.parent).as_posix()
            errors.append(f"banned link scheme in {rel_file}: contains '{banned}'")
    return errors


def _reachable_docs(docs_root: Path, files: list[Path]) -> set[Path]:
    by_path = {p.resolve(): p for p in files}
    start = (docs_root / "index.md").resolve()
    if start not in by_path:
        return set()

    seen: set[Path] = set()
    stack: list[Path] = [start]

    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)

        text = by_path[current].read_text(encoding="utf-8", errors="ignore")
        for _, resolved in _local_targets(by_path[current], text):
            if resolved in by_path and resolved not in seen:
                stack.append(resolved)

    return seen


def check_orphans(docs_root: Path, files: list[Path]) -> list[str]:
    errors: list[str] = []
    reachable = _reachable_docs(docs_root, files)
    if not reachable:
        return ["missing docs/index.md or unable to traverse docs graph"]

    for file_path in files:
        relative = file_path.relative_to(docs_root)
        if any(relative.is_relative_to(prefix) for prefix in ARCHIVED_DOC_PREFIXES):
            continue
        if file_path.resolve() not in reachable:
            rel_file = file_path.relative_to(docs_root.parent).as_posix()
            errors.append(f"orphan active doc: {rel_file}")
    return errors


def check_archived_paths(docs_root: Path) -> list[str]:
    errors: list[str] = []

    for dirname in LEGACY_DOC_DIRS:
        candidate = docs_root / dirname
        if candidate.exists():
            errors.append(f"archived docs directory still present: {candidate}")

    for marker in LEGACY_EXPLANATION_MARKERS:
        candidate = docs_root / marker
        if candidate.exists():
            errors.append(f"archived explanation artifact still present: {candidate}")

    return errors


def check_canonical_environment_sets(repo_root: Path) -> list[str]:
    """Keep the canonical Run Environment declaration aligned across durable docs."""
    errors: list[str] = []
    expected = sorted(CANONICAL_RUN_ENVIRONMENTS)

    for relative_path in CANONICAL_ENVIRONMENT_DOCS:
        file_path = repo_root / relative_path
        if not file_path.is_file():
            errors.append(f"missing canonical environment document: {relative_path.as_posix()}")
            continue

        text = file_path.read_text(encoding="utf-8", errors="ignore")
        match = CANONICAL_ENVIRONMENT_DECLARATION.search(text)
        if match is None:
            errors.append(
                f"missing canonical Run Environment declaration in {relative_path.as_posix()}; expected {expected}"
            )
            continue

        actual = frozenset(INLINE_CODE_VALUE.findall(match.group("values")))
        if actual != CANONICAL_RUN_ENVIRONMENTS:
            errors.append(
                f"canonical Run Environment drift in {relative_path.as_posix()}: "
                f"expected {expected}, found {sorted(actual)}"
            )

    return errors


def check_contract_sanity(repo_root: Path) -> list[str]:
    errors: list[str] = []

    openapi_path = repo_root / "openapi.yaml"
    if not openapi_path.exists():
        errors.append("missing openapi.yaml")
    else:
        text = openapi_path.read_text(encoding="utf-8", errors="ignore")
        if not OPENAPI_PATH_PATTERN.search(text):
            errors.append("openapi.yaml has no path entries")

    for command in CLI_CONTRACT_COMMANDS:
        try:
            proc = subprocess.run(
                command,
                cwd=repo_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            errors.append(f"failed to run {' '.join(command)}: {exc}")
            continue

        if proc.returncode != 0:
            snippet = (proc.stderr or proc.stdout).strip().splitlines()
            tail = snippet[-1] if snippet else "no output"
            errors.append(f"command failed ({proc.returncode}): {' '.join(command)} :: {tail}")

    return errors


def run_checks(repo_root: Path, *, include_contract_checks: bool = True) -> list[str]:
    docs_root = repo_root / "docs"
    if not docs_root.exists():
        return ["missing docs/ directory"]

    files = iter_docs_files(docs_root)
    if not files:
        return ["no markdown files found under docs/"]

    errors: list[str] = []
    errors.extend(check_internal_links(docs_root, files))
    errors.extend(check_banned_link_schemes(docs_root, files))
    errors.extend(check_orphans(docs_root, files))
    errors.extend(check_archived_paths(docs_root))
    errors.extend(check_canonical_environment_sets(repo_root))

    if include_contract_checks:
        errors.extend(check_contract_sanity(repo_root))

    return errors


def build_parser() -> argparse.ArgumentParser:
    """Build and return an ArgumentParser for docs quality checks.

    Returns an ArgumentParser with no required parameters that validates
    documentation links, orphan detection, and contract sanity. Supports
    --repo-root for custom repository location and --skip-contract-checks
    to skip CLI/OpenAPI validation.
    """
    parser = argparse.ArgumentParser(description="Run quality checks against active docs/")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing docs/ and generated contracts",
    )
    parser.add_argument(
        "--skip-contract-checks",
        action="store_true",
        help="Skip CLI/OpenAPI contract checks and only validate docs graph and links",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    errors = run_checks(repo_root, include_contract_checks=not args.skip_contract_checks)

    if errors:
        print("ERROR: docs quality checks failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("OK: docs quality checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
