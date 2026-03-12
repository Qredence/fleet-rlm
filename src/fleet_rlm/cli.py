"""Command-line interface for fleet-rlm runtimes.

This module provides a Typer-based CLI for running RLM workflows
and diagnostics. Commands are organized by use case:

Core commands:
    - chat: Standalone in-process interactive terminal chat
    - serve-api: Optional FastAPI server surface
    - serve-mcp: Optional FastMCP server surface
    - init: Bootstrap Claude Code scaffold assets
    - daytona-smoke: Native Daytona runtime smoke validation
    - daytona-rlm: Experimental Daytona-backed strict-RLM pilot

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
from omegaconf import OmegaConf

from .cli_commands.init_cmd import register_init_command
from .cli_commands.serve_cmds import register_serve_commands
from .config import AppConfig
from .terminal.chat import TerminalChatOptions, run_terminal_chat

# We use a global variable to store the hydra config so Typer commands can access it
# This is a common pattern when combining Hydra (app wrapper) with Typer (subcommands)
_CONFIG: AppConfig | None = None
DEFAULT_SERVER_VOLUME_NAME = "rlm-volume-dspy"

app = typer.Typer(help="Run fleet-rlm demos and experimental runtimes.")


def _resolve_server_volume_name(config: AppConfig) -> str | None:
    """Resolve the volume name from config, falling back to default."""
    volume_name = config.interpreter.volume_name
    return volume_name if volume_name is not None else DEFAULT_SERVER_VOLUME_NAME


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


@app.command("daytona-rlm")
def daytona_rlm(
    repo: str | None = typer.Option(
        None,
        "--repo",
        help="Optional repository URL to clone into the Daytona sandbox.",
    ),
    context_path: list[Path] = typer.Option(
        [],
        "--context-path",
        help="Optional local file or directory path to stage into the Daytona workspace. Repeat for multiple paths.",
    ),
    task: str = typer.Option(
        ...,
        "--task",
        help="Task for the experimental Daytona-backed RLM pilot.",
    ),
    ref: str | None = typer.Option(
        None,
        "--ref",
        help="Optional branch or commit SHA to checkout after clone.",
    ),
    max_sandboxes: int = typer.Option(
        50,
        "--max-sandboxes",
        min=1,
        help="Global sandbox budget across the rollout.",
    ),
    max_depth: int = typer.Option(
        2,
        "--max-depth",
        min=0,
        help="Maximum recursive depth for child Daytona subcalls.",
    ),
    max_iterations: int = typer.Option(
        50,
        "--max-iterations",
        min=1,
        help="Maximum number of root/child iterations per node.",
    ),
    global_timeout: int = typer.Option(
        3600,
        "--global-timeout",
        min=1,
        help="Global timeout for the entire rollout in seconds.",
    ),
    result_truncation_limit: int = typer.Option(
        10000,
        "--result-truncation-limit",
        min=1,
        help="Maximum child-result size before truncation.",
    ),
    batch_concurrency: int = typer.Option(
        4,
        "--batch-concurrency",
        min=1,
        help="Maximum concurrent child Daytona subcalls in rlm_query_batched.",
    ),
    output_dir: Path = typer.Option(
        Path("results/daytona-rlm"),
        "--output-dir",
        help="Directory for persisted rollout JSON artifacts.",
    ),
) -> None:
    """Run the experimental Daytona-backed strict-RLM pilot."""
    try:
        from .daytona_rlm import RolloutBudget, run_daytona_rlm_pilot

        if ref and not repo:
            raise ValueError("--ref requires --repo.")

        budget = RolloutBudget(
            max_sandboxes=max_sandboxes,
            max_depth=max_depth,
            max_iterations=max_iterations,
            global_timeout=global_timeout,
            result_truncation_limit=result_truncation_limit,
            batch_concurrency=batch_concurrency,
        )
        result = run_daytona_rlm_pilot(
            repo=repo,
            ref=ref,
            context_paths=[str(path) for path in context_path],
            task=task,
            budget=budget,
            output_dir=output_dir,
        )
    except Exception as exc:
        _handle_error(exc)
        return

    typer.echo(f"run_id: {result.run_id}")
    if result.result_path:
        typer.echo(f"result_path: {result.result_path}")
    if result.final_artifact is not None:
        typer.echo(
            json.dumps(
                result.final_artifact.to_dict(),
                indent=2,
                ensure_ascii=False,
            )
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
        from .daytona_rlm import run_daytona_smoke

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


def _initialize_config(overrides: list[str] | None = None) -> AppConfig:
    """Initialize Hydra config manually without taking over argument parsing."""
    from hydra import compose, initialize_config_module

    with initialize_config_module(config_module="fleet_rlm.conf", version_base=None):
        cfg = compose(config_name="config", overrides=overrides or [])
        cfg_dict = OmegaConf.to_container(cfg, resolve=True)
        if not isinstance(cfg_dict, dict):
            raise ValueError("Hydra config must resolve to a mapping")
        normalized_cfg = {str(k): v for k, v in cfg_dict.items()}
        return AppConfig(**normalized_cfg)


def main() -> None:
    """Entry point that runs Typer with optional Hydra config initialization."""
    global _CONFIG

    # Parse args to find Hydra overrides (key=value) and separate from Typer args
    hydra_overrides: list[str] = []
    typer_args: list[str] = []

    for arg in sys.argv[1:]:
        if "=" in arg and not arg.startswith("-"):
            hydra_overrides.append(arg)
        else:
            typer_args.append(arg)

    # Initialize config (with optional overrides)
    try:
        _CONFIG = _initialize_config(hydra_overrides)
    except Exception as e:
        print(f"Configuration Error: {e}", file=sys.stderr)
        raise typer.Exit(code=1)

    app(typer_args)


if __name__ == "__main__":
    main()
