from __future__ import annotations

import pytest
from typer.testing import CliRunner

RUNNER = CliRunner()


def test_app_registers_expected_commands() -> None:
    from fleet_rlm.cli import fleet_cli

    command_names = {command.name for command in fleet_cli.app.registered_commands}

    assert {"chat", "serve-api", "optimize", "daytona-smoke", "daytona-snapshot"}.issubset(command_names)


def test_help_exits_with_zero() -> None:
    from fleet_rlm.cli.fleet_cli import app

    result = RUNNER.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "serve-api" in result.output
    assert "chat" in result.output


def test_register_command_wraps_unhandled_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import typer

    from fleet_rlm.cli import fleet_cli

    temp_app = typer.Typer()
    monkeypatch.setattr(fleet_cli, "app", temp_app)

    def explode() -> None:
        raise RuntimeError("boom")

    fleet_cli._register_command("explode", explode)
    result = RUNNER.invoke(temp_app, [])

    assert result.exit_code == 1
    assert "Error: boom" in result.output


def test_register_command_preserves_typer_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    import typer

    from fleet_rlm.cli import fleet_cli

    temp_app = typer.Typer()
    monkeypatch.setattr(fleet_cli, "app", temp_app)

    def controlled_exit() -> None:
        raise typer.Exit(code=7)

    fleet_cli._register_command("controlled-exit", controlled_exit)
    result = RUNNER.invoke(temp_app, [])

    assert result.exit_code == 7
    assert "Error:" not in result.output
