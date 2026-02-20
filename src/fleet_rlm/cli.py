"""Command-line interface for DSPy RLM with Modal.

This module provides a Typer-based CLI for running RLM demonstrations
and diagnostics. Commands are organized by use case:

Core commands:
    - chat: Standalone in-process interactive terminal chat
    - code-chat: Interactive DSPy ReAct + RLM terminal UI
    - run-react-chat: Backward-compatible alias for code-chat
    - serve-api: Optional FastAPI server surface
    - serve-mcp: Optional FastMCP server surface
    - init: Bootstrap Claude Code scaffold assets

Demo commands (registered from cli_demos):
    - run-basic, run-architecture, run-api-endpoints, etc.

Usage:
    # Use Hydra syntax for configuration overrides
    $ python -m fleet_rlm.cli agent.model=gpt-4-turbo timeout=1200
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer
from omegaconf import OmegaConf

from .cli_commands.init_cmd import register_init_command
from .cli_commands.serve_cmds import register_serve_commands
from .cli_demos import register_demo_commands
from .config import AppConfig
from .terminal_chat import TerminalChatOptions, run_terminal_chat

# We use a global variable to store the hydra config so Typer commands can access it
# This is a common pattern when combining Hydra (app wrapper) with Typer (subcommands)
_CONFIG: AppConfig | None = None
DEFAULT_SERVER_VOLUME_NAME = "rlm-volume-dspy"

app = typer.Typer(help="Run DSPy RLM demos backed by a Modal sandbox.")


def _find_opentui_frontend() -> tuple[Path, Path]:
    """Locate the OpenTUI directory and entrypoint.

    Searches from both the current working directory and this module location so
    standard ``src/`` repo layouts resolve correctly.
    """
    module_path = Path(__file__).resolve()
    search_roots: list[Path] = []
    for root in [
        Path.cwd(),
        *Path.cwd().parents,
        module_path.parent,
        *module_path.parents,
    ]:
        if root not in search_roots:
            search_roots.append(root)

    for root in search_roots:
        for tui_dir in (root / "tui-cli" / "opentui-rlm", root / "tui"):
            tui_entry = tui_dir / "src" / "index.tsx"
            if tui_entry.is_file():
                return tui_dir, tui_entry

    fallback_dir = module_path.parents[2] / "tui-cli" / "opentui-rlm"
    return fallback_dir, fallback_dir / "src" / "index.tsx"


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


def _run_code_chat_session(
    *,
    docs_path: Path | None,
    config: AppConfig,
    trace: bool | None,
    trace_mode: str | None,
    no_stream: bool,
    stream_refresh_ms: int,
    opentui: bool,
) -> None:
    """Run the code-chat session, optionally with OpenTUI frontend."""
    if opentui:
        # Check for Bun availability
        bun_path = shutil.which("bun")
        if not bun_path:
            typer.echo(
                "Error: Bun runtime not found. Install from https://bun.sh",
                err=True,
            )
            raise typer.Exit(code=2)

        # Locate OpenTUI frontend in standard repo layouts.
        tui_dir, tui_entry = _find_opentui_frontend()

        if not tui_entry.exists():
            typer.echo(
                f"Error: OpenTUI frontend not found at {tui_entry}",
                err=True,
            )
            raise typer.Exit(code=2)

        # Verify backend server is running
        import urllib.request

        server_url = "http://localhost:8000/health"
        try:
            with urllib.request.urlopen(server_url, timeout=2) as response:
                if response.status != 200:
                    raise Exception("Server not healthy")
        except Exception:
            typer.echo(
                "Error: Backend server not running. Start it first with:",
                err=True,
            )
            typer.echo("  uv run fleet-rlm serve-api", err=True)
            raise typer.Exit(code=2)

        # Build environment for subprocess
        env = {"WS_URL": "ws://localhost:8000/ws/chat"}

        try:
            typer.echo(f"Starting OpenTUI frontend from {tui_dir}...")
            result = subprocess.run(
                [bun_path, "run", str(tui_entry)],
                cwd=str(tui_dir),
                env={**os.environ, **env},
                check=False,
            )
            raise typer.Exit(code=result.returncode)
        except KeyboardInterrupt:
            typer.echo("\nOpenTUI session interrupted.", err=True)
            raise typer.Exit(code=130)
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(f"Error running OpenTUI: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    typer.echo(
        "Error: OpenTUI is the only supported interactive runtime. Run with --opentui.",
        err=True,
    )
    raise typer.Exit(code=2)


# --- Register extracted commands ---
register_demo_commands(app, _print_result=_print_result, _handle_error=_handle_error)
register_init_command(app, _handle_error=_handle_error)
register_serve_commands(app, get_config=lambda: _CONFIG, _handle_error=_handle_error)


# --- Chat commands (remain inline for simplicity) ---


@app.command("code-chat")
def code_chat(
    docs_path: Path | None = typer.Option(
        None,
        "--docs-path",
        help="Optional document path to preload as active context",
    ),
    trace: bool | None = typer.Option(
        None, "--trace/--no-trace", help="Print ReAct trajectory for each turn"
    ),
    trace_mode: str | None = typer.Option(
        None,
        "--trace-mode",
        help="Trace display mode: compact, verbose, or off",
    ),
    no_stream: bool = typer.Option(False, "--no-stream", help="Disable DSPy streaming"),
    stream_refresh_ms: int = typer.Option(
        40,
        "--stream-refresh-ms",
        help="UI refresh cadence for streamed updates in milliseconds",
    ),
    opentui: bool = typer.Option(
        True,
        "--opentui/--no-opentui",
        help="Use OpenTUI React frontend (requires Bun runtime). Default: on.",
    ),
) -> None:
    """Start coding-first interactive DSPy ReAct + RLM terminal UI.

    Uses the OpenTUI React frontend.
    Configuration overrides can be passed via Hydra syntax before the command.
    """
    global _CONFIG
    if _CONFIG is None:
        typer.echo(
            "Error: Config not initialized. Run via python -m fleet_rlm.cli", err=True
        )
        raise typer.Exit(code=1)

    try:
        _run_code_chat_session(
            docs_path=docs_path,
            config=_CONFIG,
            trace=trace,
            trace_mode=trace_mode,
            no_stream=no_stream,
            stream_refresh_ms=stream_refresh_ms,
            opentui=opentui,
        )
    except Exception as exc:
        _handle_error(exc)


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


@app.command("run-react-chat")
def run_react_chat(
    docs_path: Path | None = typer.Option(None, "--docs-path"),
    trace: bool | None = typer.Option(None, "--trace/--no-trace"),
    trace_mode: str | None = typer.Option(None, "--trace-mode"),
    no_stream: bool = typer.Option(False, "--no-stream"),
    stream_refresh_ms: int = typer.Option(40, "--stream-refresh-ms"),
    opentui: bool = typer.Option(True, "--opentui/--no-opentui"),
) -> None:
    """Backward-compatible alias for `code-chat`."""
    code_chat(
        docs_path=docs_path,
        trace=trace,
        trace_mode=trace_mode,
        no_stream=no_stream,
        stream_refresh_ms=stream_refresh_ms,
        opentui=opentui,
    )


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
