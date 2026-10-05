"""Unit tests for scripts/check_repo_hygiene.py: references, freshness, and docs."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import check_repo_hygiene as hygiene
from scripts.check_repo_hygiene import (
    DOCS_CANONICAL_ENVIRONMENT_DOCS as CANONICAL_ENVIRONMENT_DOCS,
)
from scripts.check_repo_hygiene import (
    AgentsMdValidator,
    HarnessChecker,
    docs_check_archived_paths,
)
from scripts.check_repo_hygiene import (
    docs_check_canonical_environment_sets as check_canonical_environment_sets,
)

PREFLIGHT = Path(__file__).resolve().parents[2] / ".codex/cloud-preflight.zsh"


def _write(path: Path, content: str = "# test\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_environment_docs(repo_root: Path, declaration: str) -> None:
    for relative_path in CANONICAL_ENVIRONMENT_DOCS:
        file_path = repo_root / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f"# Test\n\n{declaration}\n", encoding="utf-8")


# --- Script & Retired Command Reference Checks ---


def test_active_script_references_must_resolve(tmp_path: Path) -> None:
    guide = tmp_path / "docs/how-to-guides/scripts.md"
    guide.parent.mkdir(parents=True)
    guide.write_text("Use `scripts/current.py` and `scripts/removed.py`.\n", encoding="utf-8")
    current = tmp_path / "scripts/current.py"
    current.parent.mkdir()
    current.write_text("# helper\n", encoding="utf-8")

    assert hygiene.check_active_script_references(tmp_path) == [
        "docs/how-to-guides/scripts.md: references missing script scripts/removed.py"
    ]


def test_active_reference_scan_includes_workflows_bootstrap_and_skills(tmp_path: Path) -> None:
    files = {
        ".github/workflows/ci.yml": "run: uv run python scripts/from-workflow.py\n",
        ".codex/cloud-preflight.zsh": "uv run python scripts/from-bootstrap.py\n",
        ".agents/skills/example/SKILL.md": "Follow the Fleet script guidance.\n",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    errors = hygiene.check_active_script_references(tmp_path)
    assert len(errors) == 2
    assert any(".github/workflows/ci.yml" in error for error in errors)
    assert any(".codex/cloud-preflight.zsh" in error for error in errors)


def test_retired_commands_are_rejected_from_skills(tmp_path: Path) -> None:
    skill = tmp_path / ".agents/skills/example/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Do not run `scripts/live_phase5_verify.py`.\n", encoding="utf-8")

    assert hygiene.check_retired_commands_absent(tmp_path) == [
        ".agents/skills/example/SKILL.md: retired command remains active: scripts/live_phase5_verify.py"
    ]


def test_retired_command_is_rejected_from_active_guides(tmp_path: Path) -> None:
    guide = tmp_path / "docs/how-to-guides/old.md"
    guide.parent.mkdir(parents=True)
    guide.write_text("Run scripts/live_phase5_verify.py\n", encoding="utf-8")

    errors = hygiene.check_retired_commands_absent(tmp_path)
    assert errors == ["docs/how-to-guides/old.md: retired command remains active: scripts/live_phase5_verify.py"]


# --- Agents.md Freshness & Links ---


def test_unexpected_nested_agent_guide_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "AGENTS.md", "# Root\n\n- `tools/fleet-tui/AGENTS.md`\n")
    _write(
        tmp_path / "tools/fleet-tui/AGENTS.md",
        "# TUI\n\nSee [AGENTS.md](../../AGENTS.md).\n",
    )
    _write(
        tmp_path / "src/package/AGENTS.md",
        "# Client\n\nSee [AGENTS.md](../../AGENTS.md).\n",
    )
    _write(tmp_path / "CLAUDE.md", "@AGENTS.md\n")
    _write(tmp_path / AgentsMdValidator.DEVELOPMENT_SKILL, "# Skill\n")
    _write(tmp_path / "ARCHITECTURE.md", "# Architecture\n")
    _write(tmp_path / "Makefile", "check:\n\t@true\n")

    errors = AgentsMdValidator(tmp_path).validate_all()
    assert any(error.file == "src/package/AGENTS.md" and error.issue == "unexpected_nested_guide" for error in errors)


def test_broken_guide_link(tmp_path: Path) -> None:
    validator = AgentsMdValidator(tmp_path)
    validator._check_links(tmp_path / "AGENTS.md", "[Architecture](missing.md#ownership)")
    assert [error.issue for error in validator.errors] == ["broken_link"]


def test_skill_references_are_reachable_and_links_resolve(tmp_path: Path) -> None:
    entry = tmp_path / AgentsMdValidator.DEVELOPMENT_SKILL
    _write(entry, "[Loop](references/loop.md)\n[Root](../../../AGENTS.md)")
    _write(tmp_path / "AGENTS.md", "# Root")
    _write(entry.parent / "references/loop.md", "[Broker](broker.md#timing)")
    _write(entry.parent / "references/broker.md", "[Loop](loop.md)\n[API](https://example.com)")
    validator = AgentsMdValidator(tmp_path)
    validator._validate_development_skill()
    assert validator.errors == []


# --- Documentation Quality & Environment Set Checks ---


def test_canonical_environment_sets_accept_matching_durable_docs(tmp_path: Path) -> None:
    _write_environment_docs(tmp_path, "Canonical Run Environment set: `daytona`.")
    assert check_canonical_environment_sets(tmp_path) == []


def test_canonical_environment_sets_report_a_mismatched_document(tmp_path: Path) -> None:
    _write_environment_docs(tmp_path, "Canonical Run Environment set: `daytona`.")
    drifted_path = tmp_path / "ARCHITECTURE.md"
    drifted_path.write_text("Canonical Run Environment set: `daytona`, `local`.\n", encoding="utf-8")
    assert check_canonical_environment_sets(tmp_path) == [
        "canonical Run Environment drift in ARCHITECTURE.md: expected ['daytona'], found ['daytona', 'local']"
    ]


def test_canonical_environment_sets_report_a_missing_declaration(tmp_path: Path) -> None:
    _write_environment_docs(tmp_path, "Canonical Run Environment set: `daytona`.")
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


# --- Harness Engineering Checks ---


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
