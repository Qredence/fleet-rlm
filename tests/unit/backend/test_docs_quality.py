"""Focused contracts for durable documentation drift checks."""

from __future__ import annotations

from pathlib import Path

from scripts.check_docs_quality import (
    CANONICAL_ENVIRONMENT_DOCS,
    check_canonical_environment_sets,
)


def _write_environment_docs(repo_root: Path, declaration: str) -> None:
    for relative_path in CANONICAL_ENVIRONMENT_DOCS:
        file_path = repo_root / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f"# Test\n\n{declaration}\n", encoding="utf-8")


def test_canonical_environment_sets_accept_matching_durable_docs(tmp_path: Path) -> None:
    _write_environment_docs(
        tmp_path,
        "Canonical Run Environment set: `hermetic`, `deno`, `daytona`.",
    )

    assert check_canonical_environment_sets(tmp_path) == []


def test_canonical_environment_sets_report_a_mismatched_document(tmp_path: Path) -> None:
    _write_environment_docs(
        tmp_path,
        "Canonical Run Environment set: `hermetic`, `deno`, `daytona`.",
    )
    drifted_path = tmp_path / "docs/architecture.md"
    drifted_path.write_text(
        "Canonical Run Environment set: `hermetic`, `daytona`.\n",
        encoding="utf-8",
    )

    assert check_canonical_environment_sets(tmp_path) == [
        "canonical Run Environment drift in docs/architecture.md: "
        "expected ['daytona', 'deno', 'hermetic'], found ['daytona', 'hermetic']"
    ]


def test_canonical_environment_sets_report_a_missing_declaration(tmp_path: Path) -> None:
    _write_environment_docs(
        tmp_path,
        "Canonical Run Environment set: `hermetic`, `deno`, `daytona`.",
    )
    product_path = tmp_path / "PRODUCT.md"
    product_path.write_text("# Product\n", encoding="utf-8")

    assert check_canonical_environment_sets(tmp_path) == [
        "missing canonical Run Environment declaration in PRODUCT.md; expected ['daytona', 'deno', 'hermetic']"
    ]
