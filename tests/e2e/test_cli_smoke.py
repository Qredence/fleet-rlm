from __future__ import annotations

from pathlib import Path
import re

import pytest
from typer.testing import CliRunner

from fleet_rlm.cli import app, _resolve_server_volume_name
from fleet_rlm.config import AppConfig


runner = CliRunner()


@pytest.fixture(autouse=True)
def _seed_cli_config(monkeypatch):
    """Initialize CLI config for tests that invoke Typer app directly.

    In production, config is initialized by `fleet_rlm.cli.main()` before
    Typer dispatch. These tests call `app` directly, so seed a non-None value
    to exercise command logic instead of the entrypoint guardrail.
    """
    monkeypatch.setattr("fleet_rlm.cli._CONFIG", object())


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _normalized_help_text(text: str) -> str:
    cleaned = _ANSI_RE.sub("", text)
    for dash in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"):
        cleaned = cleaned.replace(dash, "-")
    return cleaned


def test_cli_help_lists_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run-basic" in result.stdout
    assert "run-architecture" in result.stdout
    assert "check-secret" in result.stdout


def test_cli_bad_docs_path_returns_error(monkeypatch):
    monkeypatch.setenv("FLEET_DEMO_TASKS_ENABLED", "true")
    result = runner.invoke(
        app,
        [
            "run-architecture",
            "--docs-path",
            "missing-docs.txt",
            "--query",
            "extract modules",
        ],
    )
    assert result.exit_code == 1
    assert (
        "Docs path does not exist" in result.stdout
        or "Docs path does not exist" in result.stderr
    )


def test_cli_requires_docs_path_for_architecture():
    result = runner.invoke(
        app,
        [
            "run-architecture",
            "--query",
            "extract modules",
        ],
    )
    assert result.exit_code == 2
    assert "docs-path" in result.stdout or "docs-path" in result.stderr


def test_init_list_shows_all_categories():
    result = runner.invoke(app, ["init", "--list"])
    assert result.exit_code == 0
    assert "Available Skills:" in result.stdout
    assert "Available Agents:" in result.stdout
    assert "Available Teams:" in result.stdout
    assert "Available Hooks:" in result.stdout


def test_init_default_installs_all_categories(tmp_path: Path):
    target = tmp_path / "claude"
    result = runner.invoke(app, ["init", "--target", str(target)])
    assert result.exit_code == 0
    assert (target / "skills").exists()
    assert (target / "agents").exists()
    assert (target / "teams").exists()
    assert (target / "hooks").exists()


def test_init_teams_only(tmp_path: Path):
    target = tmp_path / "claude"
    result = runner.invoke(app, ["init", "--target", str(target), "--teams-only"])
    assert result.exit_code == 0
    assert (target / "teams").exists()
    assert not (target / "skills").exists()
    assert not (target / "agents").exists()
    assert not (target / "hooks").exists()


def test_init_hooks_only(tmp_path: Path):
    target = tmp_path / "claude"
    result = runner.invoke(app, ["init", "--target", str(target), "--hooks-only"])
    assert result.exit_code == 0
    assert (target / "hooks").exists()
    assert not (target / "skills").exists()
    assert not (target / "agents").exists()
    assert not (target / "teams").exists()


def test_init_no_teams_no_hooks(tmp_path: Path):
    target = tmp_path / "claude"
    result = runner.invoke(
        app, ["init", "--target", str(target), "--no-teams", "--no-hooks"]
    )
    assert result.exit_code == 0
    assert (target / "skills").exists()
    assert (target / "agents").exists()
    assert not (target / "teams").exists()
    assert not (target / "hooks").exists()


def test_init_rejects_multiple_only_modes(tmp_path: Path):
    target = tmp_path / "claude"
    result = runner.invoke(
        app, ["init", "--target", str(target), "--skills-only", "--agents-only"]
    )
    assert result.exit_code == 1
    assert (
        "Only one --*-only mode" in result.stdout
        or "Only one --*-only mode" in result.stderr
    )


def test_init_rejects_only_mode_with_exclusion(tmp_path: Path):
    target = tmp_path / "claude"
    result = runner.invoke(
        app, ["init", "--target", str(target), "--teams-only", "--no-hooks"]
    )
    assert result.exit_code == 1
    assert (
        "--*-only modes cannot be combined" in result.stdout
        or "--*-only modes cannot be combined" in result.stderr
    )


def test_resolve_server_volume_name_defaults_to_persistent_volume():
    config = AppConfig()
    assert _resolve_server_volume_name(config) == "rlm-volume-dspy"


def test_resolve_server_volume_name_preserves_configured_volume():
    config = AppConfig(
        interpreter={"volume_name": "custom-volume"},
    )
    assert _resolve_server_volume_name(config) == "custom-volume"
