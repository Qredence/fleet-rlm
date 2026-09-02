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
