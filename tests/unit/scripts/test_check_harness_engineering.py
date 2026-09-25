from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.check_harness_engineering import HarnessChecker


def _write(path: Path, content: str = "# guide\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_harness_requires_root_architecture_and_tui_guides(tmp_path: Path) -> None:
    checker = HarnessChecker(tmp_path, check_script_help=False)

    checker._check_required_guidance_files()

    missing = {(error.path, error.detail) for error in checker.errors}
    assert ("AGENTS.md", "required repository guidance file is missing") in missing
    assert ("ARCHITECTURE.md", "required repository guidance file is missing") in missing
    assert ("tools/fleet-tui/AGENTS.md", "required repository guidance file is missing") in missing


def test_harness_rejects_nested_agent_guides(tmp_path: Path) -> None:
    _write(tmp_path / "AGENTS.md")
    _write(tmp_path / "ARCHITECTURE.md")
    _write(tmp_path / "tools/fleet-tui/AGENTS.md")
    _write(tmp_path / "src/package/AGENTS.md")
    checker = HarnessChecker(tmp_path, check_script_help=False)

    checker._check_agent_guide_structure()

    assert len(checker.errors) == 1
    assert checker.errors[0].path == "src/package/AGENTS.md"
    assert checker.errors[0].detail == "unexpected nested AGENTS.md; only the TUI guide is allowed"


def test_harness_inventory_covers_nested_scripts_and_data(tmp_path: Path) -> None:
    inventory = tmp_path / "scripts/README.md"
    source = tmp_path / "scripts/benchmarks/helper.py"
    fixture = tmp_path / "scripts/benchmarks/cases.json"
    _write(inventory, "`scripts/benchmarks/helper.py`\n")
    _write(source, "# helper\n")
    _write(fixture, "[]\n")
    checker = HarnessChecker(tmp_path, check_script_help=False)

    checker._check_script_inventory()

    assert [(error.path, error.detail) for error in checker.errors] == [
        ("scripts/benchmarks/cases.json", "script or data file is missing from scripts/README.md")
    ]


def test_harness_default_skips_root_agents_line_budget(tmp_path: Path) -> None:
    over_budget = "\n".join(f"line {index}" for index in range(200))
    _write(tmp_path / "AGENTS.md", over_budget)
    checker = HarnessChecker(tmp_path, check_script_help=False)

    checker.run()

    assert not any(error.path == "AGENTS.md" and "budget" in error.detail for error in checker.errors)


def test_harness_default_skips_docs_index_link_check(tmp_path: Path) -> None:
    _write(tmp_path / "docs/index.md", "# docs\n")
    checker = HarnessChecker(tmp_path, check_script_help=False)

    checker.run()

    assert not any(error.path == "docs/index.md" for error in checker.errors)


def test_harness_editorial_enforces_docs_index_link_check(tmp_path: Path) -> None:
    _write(tmp_path / "docs/index.md", "# docs\n")
    _write(tmp_path / "docs/SUMMARY.md", "# summary\n")
    checker = HarnessChecker(tmp_path, check_script_help=False, editorial=True)

    checker._check_docs_index_links()

    index_errors = [error for error in checker.errors if error.path == "docs/index.md"]
    assert len(index_errors) == 1
    assert index_errors[0].detail == "does not link ../ARCHITECTURE.md"


def test_harness_editorial_enforces_root_agents_line_budget(tmp_path: Path) -> None:
    over_budget = "\n".join(f"line {index}" for index in range(200))
    _write(tmp_path / "AGENTS.md", over_budget)
    checker = HarnessChecker(tmp_path, check_script_help=False, editorial=True)

    checker._check_root_agents_budget()

    assert len(checker.errors) == 1
    assert checker.errors[0].path == "AGENTS.md"
    assert "budget" in checker.errors[0].detail


def test_harness_rejects_untracked_nested_agent_guides_in_a_git_checkout(tmp_path: Path) -> None:
    _write(tmp_path / "AGENTS.md")
    _write(tmp_path / "ARCHITECTURE.md")
    _write(tmp_path / "tools/fleet-tui/AGENTS.md")
    subprocess.run(("git", "init", "--quiet"), cwd=tmp_path, check=True)
    subprocess.run(
        ("git", "add", "AGENTS.md", "ARCHITECTURE.md", "tools/fleet-tui/AGENTS.md"),
        cwd=tmp_path,
        check=True,
    )
    _write(tmp_path / "src/package/AGENTS.md")
    checker = HarnessChecker(tmp_path, check_script_help=False)

    checker._check_agent_guide_structure()

    assert len(checker.errors) == 1
    assert checker.errors[0].path == "src/package/AGENTS.md"
