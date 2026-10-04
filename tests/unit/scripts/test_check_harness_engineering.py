from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.check_repo_hygiene import HarnessChecker

PREFLIGHT = Path(__file__).resolve().parents[3] / ".codex/cloud-preflight.zsh"


def _write(path: Path, content: str = "# guide\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


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


def _git(repo: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True)


def _preflight(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("zsh", str(PREFLIGHT), *args),
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(shutil.which("zsh") is None, reason="Codex Cloud preflight requires zsh")
def test_cloud_preflight_branch_and_argument_guards(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "base")

    assert "must not run from main" in _preflight(repo, "--skip-harness").stderr
    assert _preflight(repo, "--unknown").returncode == 2

    _git(repo, "checkout", "-b", "feature")
    assert "origin/main is unavailable" in _preflight(repo, "--skip-harness").stderr

    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    accepted = _preflight(repo, "--skip-harness")
    assert accepted.returncode == 0, accepted.stderr
    assert "current=feature base=origin/main" in accepted.stdout

    _git(repo, "checkout", "main")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "new-base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "checkout", "feature")
    assert "not based on origin/main" in _preflight(repo, "--skip-harness").stderr

    _git(repo, "checkout", "--detach")
    assert "detached HEAD" in _preflight(repo, "--skip-harness").stderr


@pytest.mark.skipif(shutil.which("zsh") is None, reason="Codex Cloud preflight requires zsh")
def test_cloud_preflight_runs_harness_only_without_skip(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "base")
    _git(repo, "checkout", "-b", "feature")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$UV_LOG"\n', encoding="utf-8")
    fake_uv.chmod(0o755)
    uv_log = tmp_path / "uv.log"
    env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "UV_LOG": str(uv_log)}

    skipped = _preflight(repo, "--skip-harness", env=env)
    assert skipped.returncode == 0, skipped.stderr
    assert not uv_log.exists()

    checked = _preflight(repo, env=env)
    assert checked.returncode == 0, checked.stderr
    assert uv_log.read_text(encoding="utf-8").strip() == ("run python scripts/check_repo_hygiene.py --skip-script-help")
