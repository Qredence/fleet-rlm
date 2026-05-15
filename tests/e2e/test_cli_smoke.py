from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from fleet_rlm.cli import app
from fleet_rlm.cli.config import set_current_app_config
from fleet_rlm.integrations.config.env import AppConfig

runner = CliRunner()


@pytest.fixture(autouse=True)
def _seed_cli_config():
    """Initialize CLI config for tests that invoke Typer app directly.

    In production, config is initialized by `fleet_rlm.cli.main()` before
    Typer dispatch. These tests call `app` directly, so seed a non-None value
    to exercise command logic instead of the entrypoint guardrail.
    """
    set_current_app_config(AppConfig())
    yield
    set_current_app_config(None)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _normalized_help_text(text: str) -> str:
    cleaned = _ANSI_RE.sub("", text)
    for dash in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"):
        cleaned = cleaned.replace(dash, "-")
    return cleaned


def test_cli_help_lists_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    help_text = _normalized_help_text(result.stdout)
    for command in ("serve-api", "chat", "daytona-smoke", "daytona-snapshot"):
        assert command in help_text


def test_daytona_smoke_help_exposes_repo_and_ref_options():
    result = runner.invoke(app, ["daytona-smoke", "--help"])
    assert result.exit_code == 0
    help_text = _normalized_help_text(result.stdout)
    assert "--repo" in help_text
    assert "--ref" in help_text


def test_daytona_snapshot_help_exposes_bootstrap_options():
    result = runner.invoke(app, ["daytona-snapshot", "--help"])
    assert result.exit_code == 0
    help_text = _normalized_help_text(result.stdout)
    assert "--name" in help_text
    assert "--base-image" in help_text
    assert "--refresh" in help_text


def test_optimize_list_does_not_require_dataset():
    result = runner.invoke(app, ["optimize", "list"])
    assert result.exit_code == 0
    assert "Available modules:" in result.stdout
