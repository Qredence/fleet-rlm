"""Tests for the optimize CLI command."""

from __future__ import annotations

from typer.testing import CliRunner

from fleet_rlm.cli.fleet_cli import app

runner = CliRunner()


def test_optimize_list_includes_longcot_reasoner() -> None:
    """VAL-MOD-001: CLI list output contains registered module."""
    result = runner.invoke(app, ["optimize", "list"])
    assert result.exit_code == 0
    assert "longcot-reasoner" in result.output
