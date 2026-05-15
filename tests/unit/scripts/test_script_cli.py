"""Smoke tests for the retained script helper surface."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_SCRIPTS = (
    "scripts/build_ui.py",
    "scripts/check_agents_md_freshness.py",
    "scripts/check_docs_quality.py",
    "scripts/consolidate_rlm_results.py",
    "scripts/db_init.py",
    "scripts/db_smoke.py",
    "scripts/deployment_observability.py",
    "scripts/dev_issue_token.py",
    "scripts/evaluate_rlm_capabilities.py",
    "scripts/mlflow_cli.py",
    "scripts/oolong_official_eval.py",
    "scripts/openapi_tools.py",
    "scripts/validate_env.py",
    "scripts/validate_release.py",
    "scripts/validate_rlm_e2e_trace.py",
)


def run_script(relative_path: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / relative_path), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("relative_path", PYTHON_SCRIPTS)
def test_retained_python_scripts_support_help(relative_path: str) -> None:
    result = run_script(relative_path, "--help")
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()


def test_build_ui_help_does_not_require_pnpm() -> None:
    env = {**os.environ, "PATH": ""}
    result = run_script("scripts/build_ui.py", "--help", env=env)
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()


def test_db_init_fails_cleanly_without_database_env(tmp_path: Path) -> None:
    env = {key: value for key, value in os.environ.items() if key not in {"DATABASE_URL", "DATABASE_ADMIN_URL"}}
    result = run_script("scripts/db_init.py", "--env-file", str(tmp_path / "missing.env"), env=env)
    assert result.returncode == 1
    assert "DATABASE_ADMIN_URL or DATABASE_URL is required" in result.stderr


def test_db_smoke_fails_cleanly_without_database_env(tmp_path: Path) -> None:
    env = {key: value for key, value in os.environ.items() if key not in {"DATABASE_URL", "DATABASE_ADMIN_URL"}}
    result = run_script("scripts/db_smoke.py", "--env-file", str(tmp_path / "missing.env"), env=env)
    assert result.returncode == 1
    assert "DATABASE_URL or DATABASE_ADMIN_URL is required" in result.stderr
