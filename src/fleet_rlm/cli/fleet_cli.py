"""Command-line interface for fleet-rlm runtimes.

This module provides a Typer-based CLI for running RLM workflows
and diagnostics. Commands are organized by use case:

Core commands:
    - chat: Standalone in-process interactive terminal chat
    - serve-api: Optional FastAPI server surface
    - serve-mcp: Optional FastMCP server surface
    - init: Bootstrap Claude Code scaffold assets
    - daytona-smoke: Native Daytona runtime smoke validation

Usage:
    # Use Hydra syntax for configuration overrides
    $ python -m fleet_rlm.cli agent.model=gpt-4-turbo timeout=1200
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from fleet_rlm.integrations.config.env import AppConfig
from .config import initialize_app_config, split_hydra_overrides
from .commands.init_cmd import register_init_command
from .commands.serve_cmds import register_serve_commands

# We use a global variable to store the hydra config so Typer commands can access it
# This is a common pattern when combining Hydra (app wrapper) with Typer (subcommands)
_CONFIG: AppConfig | None = None

app = typer.Typer(
    help="Run fleet-rlm demos and experimental runtimes.",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _print_result(result: dict[str, Any], *, verbose: bool) -> None:
    """Print a result dictionary to stdout.

    Formats the output based on verbosity level. In verbose mode,
    outputs pretty-printed JSON. In non-verbose mode, outputs a
    simplified key-value format.
    """
    if verbose:
        typer.echo(json.dumps(result, indent=2, sort_keys=True))
        return

    for key, value in result.items():
        if isinstance(value, (dict, list)):
            typer.echo(f"{key}: {json.dumps(value)}")
        else:
            typer.echo(f"{key}: {value}")


def _handle_error(exc: Exception) -> None:
    """Handle an exception by printing an error message and exiting."""
    if isinstance(exc, typer.Exit):
        raise exc
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(code=1) from exc


# --- Register extracted commands ---
register_init_command(app, _handle_error=_handle_error)
register_serve_commands(app, get_config=lambda: _CONFIG, _handle_error=_handle_error)


# --- Chat commands (remain inline for simplicity) ---


@app.command("chat")
def chat(
    docs_path: Path | None = typer.Option(
        None,
        "--docs-path",
        help="Optional document path to preload as active context",
    ),
    trace: bool | None = typer.Option(
        None, "--trace/--no-trace", help="Enable verbose thought/status display"
    ),
    trace_mode: str | None = typer.Option(
        None,
        "--trace-mode",
        help="Trace display mode: compact, verbose, or off",
    ),
) -> None:
    """Start standalone in-process interactive terminal chat."""
    from fleet_rlm.cli.terminal.chat import TerminalChatOptions, run_terminal_chat

    global _CONFIG
    if _CONFIG is None:
        typer.echo(
            "Error: Config not initialized. Run via python -m fleet_rlm.cli", err=True
        )
        raise typer.Exit(code=1)

    resolved_trace_mode = trace_mode
    if resolved_trace_mode is None:
        resolved_trace_mode = "verbose" if trace else "compact"
    run_terminal_chat(
        config=_CONFIG,
        options=TerminalChatOptions(
            docs_path=docs_path,
            trace_mode=resolved_trace_mode,  # type: ignore[arg-type]
        ),
    )


@app.command("daytona-smoke")
def daytona_smoke(
    repo: str = typer.Option(
        ...,
        "--repo",
        help="Repository URL to clone into the Daytona sandbox.",
    ),
    ref: str | None = typer.Option(
        None,
        "--ref",
        help="Optional branch or commit SHA to checkout after clone.",
    ),
) -> None:
    """Run a native Daytona smoke validation without invoking an LM."""
    try:
        from fleet_rlm.integrations.providers.daytona import run_daytona_smoke

        result = run_daytona_smoke(
            repo=repo,
            ref=ref,
        )
    except Exception as exc:
        _handle_error(exc)
        return

    payload = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    if result.error_category is not None:
        typer.echo(payload, err=True)
        raise typer.Exit(code=1)
    typer.echo(payload)


def main() -> None:
    """Entry point that runs Typer with optional Hydra config initialization."""
    global _CONFIG

    hydra_overrides, typer_args = split_hydra_overrides(sys.argv[1:])

    # Help and completion output should be available without initializing runtime config.
    if any(
        arg in {"--help", "-h", "--show-completion", "--install-completion"}
        for arg in typer_args
    ):
        app(typer_args)
        return

    # Initialize config (with optional overrides)
    try:
        _CONFIG = initialize_app_config(hydra_overrides)
    except Exception as e:
        print(f"Configuration Error: {e}", file=sys.stderr)
        raise typer.Exit(code=1)

    app(typer_args)


if __name__ == "__main__":
    main()
