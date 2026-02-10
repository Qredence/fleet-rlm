from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from fleet_rlm.cli import app


runner = CliRunner()


def test_cli_help_lists_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run-basic" in result.stdout
    assert "run-architecture" in result.stdout
    assert "check-secret" in result.stdout


def test_cli_bad_docs_path_returns_error():
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
