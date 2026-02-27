#!/usr/bin/env python3
"""Quality checks for active documentation under docs/."""

from __future__ import annotations

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
LEGACY_EXPLANATION_MARKERS = (
    Path("explanation/README.md"),
    Path("explanation/architecture.md"),
    Path("explanation/rlm-concepts.md"),
    Path("explanation/stateful-architecture.md"),
    Path("explanation/memory-topology.md"),
    Path("explanation/memory-topology"),
)

CLI_CONTRACT_COMMANDS = (
    ("uv", "run", "fleet-rlm", "--help"),
    ("uv", "run", "fleet-rlm", "serve-api", "--help"),
    ("uv", "run", "fleet-rlm", "serve-mcp", "--help"),
    ("uv", "run", "fleet", "--help"),
)


def iter_docs_files(docs_root: Path) -> list[Path]:
    return sorted(p for p in docs_root.rglob("*.md") if p.is_file())


def _local_targets(file_path: Path, text: str) -> list[tuple[str, Path]]:
    links: list[tuple[str, Path]] = []
    for raw_target in LINK_PATTERN.findall(text):
        if not raw_target or raw_target.startswith(EXTERNAL_PREFIXES):
            continue
        clean = raw_target.split("#", 1)[0]
        if not clean:
            continue
        resolved = (file_path.parent / clean).resolve()
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
        if file_path.resolve() not in reachable:
            rel_file = file_path.relative_to(docs_root.parent).as_posix()
            errors.append(f"orphan active doc: {rel_file}")
    return errors


def check_legacy_paths(docs_root: Path) -> list[str]:
    errors: list[str] = []

    for dirname in LEGACY_DOC_DIRS:
        candidate = docs_root / dirname
        if candidate.exists():
            errors.append(f"legacy docs directory still present: {candidate}")

    for marker in LEGACY_EXPLANATION_MARKERS:
        candidate = docs_root / marker
        if candidate.exists():
            errors.append(f"legacy explanation artifact still present: {candidate}")

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
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            errors.append(f"failed to run {' '.join(command)}: {exc}")
            continue

        if proc.returncode != 0:
            snippet = (proc.stderr or proc.stdout).strip().splitlines()
            tail = snippet[-1] if snippet else "no output"
            errors.append(
                f"command failed ({proc.returncode}): {' '.join(command)} :: {tail}"
            )

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
    errors.extend(check_legacy_paths(docs_root))

    if include_contract_checks:
        errors.extend(check_contract_sanity(repo_root))

    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    errors = run_checks(repo_root, include_contract_checks=True)

    if errors:
        print("ERROR: docs quality checks failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("OK: docs quality checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
