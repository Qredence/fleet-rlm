from __future__ import annotations

from typer.testing import CliRunner

from rlm_dspy_modal.cli import app


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
