"""Focused contracts for durable documentation drift checks."""

from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.check_repo_hygiene import (
    DOCS_CANONICAL_ENVIRONMENT_DOCS as CANONICAL_ENVIRONMENT_DOCS,
)
from scripts.check_repo_hygiene import (
    docs_check_archived_paths,
)
from scripts.check_repo_hygiene import (
    docs_check_canonical_environment_sets as check_canonical_environment_sets,
)


def _write_environment_docs(repo_root: Path, declaration: str) -> None:
    for relative_path in CANONICAL_ENVIRONMENT_DOCS:
        file_path = repo_root / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f"# Test\n\n{declaration}\n", encoding="utf-8")


def test_canonical_environment_sets_accept_matching_durable_docs(tmp_path: Path) -> None:
    _write_environment_docs(
        tmp_path,
        "Canonical Run Environment set: `daytona`.",
    )

    assert check_canonical_environment_sets(tmp_path) == []


def test_canonical_environment_sets_report_a_mismatched_document(tmp_path: Path) -> None:
    _write_environment_docs(
        tmp_path,
        "Canonical Run Environment set: `daytona`.",
    )
    drifted_path = tmp_path / "ARCHITECTURE.md"
    drifted_path.write_text(
        "Canonical Run Environment set: `daytona`, `local`.\n",
        encoding="utf-8",
    )

    assert check_canonical_environment_sets(tmp_path) == [
        "canonical Run Environment drift in ARCHITECTURE.md: expected ['daytona'], found ['daytona', 'local']"
    ]


def test_canonical_environment_sets_report_a_missing_declaration(tmp_path: Path) -> None:
    _write_environment_docs(
        tmp_path,
        "Canonical Run Environment set: `daytona`.",
    )
    architecture_path = tmp_path / "ARCHITECTURE.md"
    architecture_path.write_text("# Architecture\n", encoding="utf-8")

    assert check_canonical_environment_sets(tmp_path) == [
        "missing canonical Run Environment declaration in ARCHITECTURE.md; expected ['daytona']"
    ]


def test_ignored_local_archive_directory_does_not_fail_documentation_checks(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    docs_root = tmp_path / "docs"
    ignored = docs_root / "plans"
    ignored.mkdir(parents=True)
    (ignored / "local-plan.md").write_text("local only\n", encoding="utf-8")

    assert docs_check_archived_paths(docs_root) == []
