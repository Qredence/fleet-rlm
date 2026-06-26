"""Command-line interface for fleet-rlm runtimes.

This module provides a Typer-based CLI for running RLM workflows
and diagnostics. Commands are organized by use case:

Core commands:
    - chat: Standalone in-process interactive terminal chat
    - serve-api: Optional FastAPI server surface
    - daytona-smoke: Native Daytona runtime smoke validation
    - daytona-snapshot: Bootstrap the reusable Daytona base snapshot

Usage:
    # Use Hydra syntax for configuration overrides
    $ python -m fleet_rlm.cli agent.model=gpt-4-turbo timeout=1200
"""

from __future__ import annotations

import json
import sys
from functools import wraps
from pathlib import Path
from typing import Any, Callable, cast

import typer

from fleet_rlm.integrations.daytona.sdk_ops import (
    DEFAULT_SNAPSHOT_BASE_IMAGE,
    DEFAULT_SNAPSHOT_NAME,
)

from .commands.eval_cmd import eval_command
from .commands.serve_cmds import serve_api_command
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


_register_command("serve-api", serve_api_command)
_register_command("eval", eval_command)


@app.command("optimize")
def optimize(
    module: str = typer.Argument(
        help="Registered module slug to optimize (use 'list' to see available modules).",
    ),
    dataset: Path | None = typer.Argument(
        None,
        help="Path to JSON or JSONL dataset.",
    ),
    output_path: Path | None = typer.Option(
        None,
        "--output-path",
        "-o",
        help="Where to save the optimized DSPy module artifact.",
    ),
    train_ratio: float = typer.Option(
        0.8,
        "--train-ratio",
        help="Training split ratio for GEPA compilation.",
    ),
    auto: str = typer.Option(
        "light",
        "--auto",
        help="Optimization intensity (light, medium, heavy).",
    ),
    skill_name: str | None = typer.Option(
        None,
        "--skill-name",
        help="Optimize a bundled or mounted Fleet skill by name instead of a registered module.",
    ),
    skill_path: Path | None = typer.Option(
        None,
        "--skill-path",
        help="Optimize a SKILL.md-compatible markdown file instead of a registered module.",
    ),
    trace_bundle_path: list[str] = typer.Option(
        [],
        "--trace-bundle-path",
        help="Offline trace bundle path available to the RLM-GEPA instruction proposer.",
    ),
    report: bool = typer.Option(
        False,
        "--report",
        help="Print a markdown report summary after optimization.",
    ),
) -> None:
    """Run offline prompt optimization for a registered DSPy module."""
    try:
        from .commands.optimize_cmd import optimize_command

        optimize_command(
            module=module,
            dataset=dataset,
            output_path=output_path,
            train_ratio=train_ratio,
            auto=auto,
            skill_name=skill_name,
            skill_path=skill_path,
            trace_bundle_path=trace_bundle_path,
            report=report,
        )
    except Exception as exc:
        _handle_error(exc)


# --- Chat commands (remain inline for simplicity) ---


@app.command("chat")
def chat(
    docs_path: Path | None = typer.Option(
        None,
        "--docs-path",
        help="Optional document path to preload as active context",
    ),
    trace: bool | None = typer.Option(None, "--trace/--no-trace", help="Enable verbose thought/status display"),
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
    from fleet_rlm.runtime.schemas import TraceMode

    config = _require_config(error_message="Error: Config not initialized. Run via python -m fleet_rlm.cli")

    if trace_mode in {"compact", "verbose", "off"}:
        resolved_trace_mode: TraceMode = cast(TraceMode, trace_mode)
    else:
        resolved_trace_mode = "verbose" if trace else "compact"
    try:
        run_terminal_chat(
            config=config,
            options=TerminalChatOptions(
                docs_path=docs_path,
                trace_mode=resolved_trace_mode,
                volume_name=volume_name,
            ),
        )
    except Exception as exc:
        _handle_error(exc)


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


@app.command("daytona-snapshot")
def daytona_snapshot(
    name: str = typer.Option(
        DEFAULT_SNAPSHOT_NAME,
        "--name",
        help="Name of the reusable Daytona base snapshot to bootstrap.",
    ),
    base_image: str = typer.Option(
        DEFAULT_SNAPSHOT_BASE_IMAGE,
        "--base-image",
        help="Base OCI image used when creating or refreshing the snapshot.",
    ),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Delete an existing snapshot with this name/id before recreating it.",
    ),
) -> None:
    """Create or refresh the reusable Daytona base snapshot used by fleet-rlm."""
    try:
        from fleet_rlm.integrations.daytona import bootstrap_snapshot

        result = bootstrap_snapshot(
            name=name,
            base_image=base_image,
            refresh=refresh,
        )
    except Exception as exc:
        _handle_error(exc)
        return

    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


def main() -> None:
    """Entry point that runs Typer with optional Hydra config initialization."""
    hydra_overrides, typer_args = split_hydra_overrides(sys.argv[1:])
    set_current_app_config(None)

    # Help and completion output should be available without initializing runtime config.
    if any(arg in {"--help", "-h", "--show-completion", "--install-completion"} for arg in typer_args):
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
