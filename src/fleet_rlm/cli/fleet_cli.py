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

from functools import wraps
import json
import sys
from pathlib import Path
from typing import Any, Callable

import typer

from .commands import init_command, serve_api_command, serve_mcp_command
from .config import (
    initialize_app_config,
    require_current_app_config,
    set_current_app_config,
    split_hydra_overrides,
)

app = typer.Typer(
    help="Run fleet-rlm demos and experimental runtimes.",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _handle_error(exc: Exception) -> None:
    """Handle an exception by printing an error message and exiting."""
    if isinstance(exc, typer.Exit):
        raise exc
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(code=1) from exc


def _register_command(name: str, callback: Callable[..., None]) -> None:
    """Register a command implementation with shared error handling."""

    @wraps(callback)
    def wrapped(*args: Any, **kwargs: Any) -> None:
        try:
            callback(*args, **kwargs)
        except Exception as exc:
            _handle_error(exc)

    app.command(name)(wrapped)


def _require_config(*, error_message: str | None = None) -> Any:
    return require_current_app_config(error_message=error_message)


_register_command("init", init_command)
_register_command("serve-api", serve_api_command)
_register_command("serve-mcp", serve_mcp_command)


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
    volume_name: str | None = typer.Option(
        None,
        "--volume-name",
        help="Optional Daytona volume name for persistent storage",
    ),
) -> None:
    """Start standalone in-process interactive terminal chat."""
    from fleet_rlm.cli.terminal.chat import TerminalChatOptions, run_terminal_chat

    config = _require_config(
        error_message="Error: Config not initialized. Run via python -m fleet_rlm.cli"
    )

    resolved_trace_mode = trace_mode
    if resolved_trace_mode is None:
        resolved_trace_mode = "verbose" if trace else "compact"
    run_terminal_chat(
        config=config,
        options=TerminalChatOptions(
            docs_path=docs_path,
            trace_mode=resolved_trace_mode,  # type: ignore[arg-type]
            volume_name=volume_name,
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
        from fleet_rlm.integrations.daytona import run_daytona_smoke

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
    hydra_overrides, typer_args = split_hydra_overrides(sys.argv[1:])
    set_current_app_config(None)

    # Help and completion output should be available without initializing runtime config.
    if any(
        arg in {"--help", "-h", "--show-completion", "--install-completion"}
        for arg in typer_args
    ):
        app(typer_args)
        return

    # Initialize config (with optional overrides)
    try:
        set_current_app_config(initialize_app_config(hydra_overrides))
    except Exception as e:
        print(f"Configuration Error: {e}", file=sys.stderr)
        raise typer.Exit(code=1)

    app(typer_args)


if __name__ == "__main__":
    main()
