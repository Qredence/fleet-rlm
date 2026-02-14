"""Command-line interface for DSPy RLM with Modal.

This module provides a Typer-based CLI for running RLM demonstrations
and diagnostics. Commands are organized by use case:

Core commands:
    - code-chat: Interactive DSPy ReAct + RLM terminal UI
    - run-react-chat: Backward-compatible alias for code-chat
    - serve-api: Optional FastAPI server surface
    - serve-mcp: Optional FastMCP server surface
    - init: Bootstrap Claude Code scaffold assets

Demo commands (registered from cli_demos):
    - run-basic, run-architecture, run-api-endpoints, etc.

Usage:
    $ python -m fleet_rlm.cli code-chat
    $ python -m fleet_rlm.cli serve-api
"""

from __future__ import annotations

import json
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import typer

from . import scaffold
from .cli_demos import register_demo_commands

app = typer.Typer(help="Run DSPy RLM demos backed by a Modal sandbox.")


def _print_result(result: dict[str, Any], *, verbose: bool) -> None:
    """Print a result dictionary to stdout.

    Formats the output based on verbosity level. In verbose mode,
    outputs pretty-printed JSON. In non-verbose mode, outputs a
    simplified key-value format.

    Args:
        result: The result dictionary to print.
        verbose: If True, print pretty-printed JSON. If False, print
            simplified key-value pairs.
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
    """Handle an exception by printing an error message and exiting.

    Args:
        exc: The exception that occurred.

    Raises:
        typer.Exit: Always raised with exit code 1 after printing the error.
    """
    if isinstance(exc, typer.Exit):
        raise exc
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(code=1) from exc


def _run_code_chat_session(
    *,
    docs_path: Path | None,
    react_max_iters: int,
    rlm_max_iterations: int,
    rlm_max_llm_calls: int,
    timeout: int,
    secret_name: str,
    volume_name: str | None,
    trace: bool | None,
    trace_mode: str | None,
    no_stream: bool,
    stream_refresh_ms: int,
    opentui: bool,
) -> None:
    # Handle OpenTUI mode
    if opentui:
        import shutil
        import subprocess
        from pathlib import Path as StdPath

        # Check for Bun availability
        bun_path = shutil.which("bun")
        if not bun_path:
            typer.echo(
                "Error: Bun runtime not found. Install from https://bun.sh",
                err=True,
            )
            raise typer.Exit(code=2)

        # Locate tui/ directory relative to package root
        package_root = StdPath(__file__).parent.parent.parent
        tui_dir = package_root / "tui"
        tui_entry = tui_dir / "src" / "index.tsx"

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
            # Spawn Bun subprocess
            typer.echo(f"Starting OpenTUI frontend from {tui_dir}...")
            import os

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
        except Exception as exc:
            typer.echo(f"Error running OpenTUI: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    typer.echo(
        "Error: OpenTUI is the only supported interactive runtime. Run with --opentui.",
        err=True,
    )
    raise typer.Exit(code=2)


# --- Demo and diagnostic commands (registered from cli_demos) ---
register_demo_commands(app, _print_result=_print_result, _handle_error=_handle_error)


@app.command("code-chat")
def code_chat(
    docs_path: Path | None = typer.Option(
        None,
        "--docs-path",
        help="Optional document path to preload as active context",
    ),
    react_max_iters: int = typer.Option(10, help="DSPy ReAct max_iters"),
    rlm_max_iterations: int = typer.Option(30, help="Internal RLM max_iterations"),
    rlm_max_llm_calls: int = typer.Option(50, help="Internal RLM max_llm_calls"),
    timeout: int = typer.Option(900, help="Modal sandbox timeout in seconds"),
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    volume_name: str | None = typer.Option(
        None, help="Modal volume name for persistent storage"
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

    REPL commands:
        /exit              Exit the session
        /history           Show current chat history
        /reset             Reset history and clear sandbox buffers
        /tools             List ReAct tools
        /load <path>       Load a document as the active context
    """
    try:
        _run_code_chat_session(
            docs_path=docs_path,
            react_max_iters=react_max_iters,
            rlm_max_iterations=rlm_max_iterations,
            rlm_max_llm_calls=rlm_max_llm_calls,
            timeout=timeout,
            secret_name=secret_name,
            volume_name=volume_name,
            trace=trace,
            trace_mode=trace_mode,
            no_stream=no_stream,
            stream_refresh_ms=stream_refresh_ms,
            opentui=opentui,
        )
    except Exception as exc:
        _handle_error(exc)


@app.command("run-react-chat")
def run_react_chat(
    docs_path: Path | None = typer.Option(
        None,
        "--docs-path",
        help="Optional document path to preload as active context",
    ),
    react_max_iters: int = typer.Option(10, help="DSPy ReAct max_iters"),
    rlm_max_iterations: int = typer.Option(30, help="Internal RLM max_iterations"),
    rlm_max_llm_calls: int = typer.Option(50, help="Internal RLM max_llm_calls"),
    timeout: int = typer.Option(900, help="Modal sandbox timeout in seconds"),
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    volume_name: str | None = typer.Option(
        None, help="Modal volume name for persistent storage"
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
    """Backward-compatible alias for `code-chat`."""
    try:
        _run_code_chat_session(
            docs_path=docs_path,
            react_max_iters=react_max_iters,
            rlm_max_iterations=rlm_max_iterations,
            rlm_max_llm_calls=rlm_max_llm_calls,
            timeout=timeout,
            secret_name=secret_name,
            volume_name=volume_name,
            trace=trace,
            trace_mode=trace_mode,
            no_stream=no_stream,
            stream_refresh_ms=stream_refresh_ms,
            opentui=opentui,
        )
    except Exception as exc:
        _handle_error(exc)


@app.command("serve-api")
def serve_api(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
    react_max_iters: int = typer.Option(10, help="DSPy ReAct max_iters"),
    rlm_max_iterations: int = typer.Option(30, help="Internal RLM max_iterations"),
    rlm_max_llm_calls: int = typer.Option(50, help="Internal RLM max_llm_calls"),
    timeout: int = typer.Option(900, help="Modal sandbox timeout in seconds"),
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    volume_name: str | None = typer.Option(
        None, help="Modal volume name for persistent storage"
    ),
) -> None:
    """Run optional FastAPI server surface (requires `--extra server`)."""
    try:
        missing = [pkg for pkg in ("fastapi", "uvicorn") if find_spec(pkg) is None]
        if missing:
            typer.echo(
                "Server dependencies missing: "
                + ", ".join(missing)
                + "\nInstall with:\n  uv sync --extra dev --extra server",
                err=True,
            )
            raise typer.Exit(code=2)

        import uvicorn

        from .server.config import ServerRuntimeConfig
        from .server.main import create_app

        app_obj = create_app(
            config=ServerRuntimeConfig(
                secret_name=secret_name,
                volume_name=volume_name,
                timeout=timeout,
                react_max_iters=react_max_iters,
                rlm_max_iterations=rlm_max_iterations,
                rlm_max_llm_calls=rlm_max_llm_calls,
            )
        )
        uvicorn.run(app_obj, host=host, port=port)
    except Exception as exc:
        _handle_error(exc)


@app.command("serve-mcp")
def serve_mcp(
    transport: str = typer.Option(
        "stdio",
        help="FastMCP transport: stdio, sse, streamable-http",
    ),
    host: str = typer.Option("127.0.0.1", help="Host for HTTP transports"),
    port: int = typer.Option(8001, help="Port for HTTP transports"),
    react_max_iters: int = typer.Option(10, help="DSPy ReAct max_iters"),
    rlm_max_iterations: int = typer.Option(30, help="Internal RLM max_iterations"),
    rlm_max_llm_calls: int = typer.Option(50, help="Internal RLM max_llm_calls"),
    timeout: int = typer.Option(900, help="Modal sandbox timeout in seconds"),
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    volume_name: str | None = typer.Option(
        None, help="Modal volume name for persistent storage"
    ),
) -> None:
    """Run optional FastMCP server surface (requires `--extra mcp`)."""
    try:
        missing = [pkg for pkg in ("fastmcp",) if find_spec(pkg) is None]
        if missing:
            typer.echo(
                "MCP dependencies missing: "
                + ", ".join(missing)
                + "\nInstall with:\n  uv sync --extra dev --extra mcp",
                err=True,
            )
            raise typer.Exit(code=2)

        from .mcp.server import MCPRuntimeConfig, create_mcp_server

        server = create_mcp_server(
            config=MCPRuntimeConfig(
                secret_name=secret_name,
                volume_name=volume_name,
                timeout=timeout,
                react_max_iters=react_max_iters,
                rlm_max_iterations=rlm_max_iterations,
                rlm_max_llm_calls=rlm_max_llm_calls,
            )
        )

        transport_norm = transport.strip().lower()
        if transport_norm == "stdio":
            server.run(transport="stdio")
        elif transport_norm in {"sse", "streamable-http"}:
            server.run(transport=transport_norm, host=host, port=port)
        else:
            typer.echo(
                "transport must be one of: stdio, sse, streamable-http", err=True
            )
            raise typer.Exit(code=2)
    except Exception as exc:
        _handle_error(exc)


@app.command("init")
def init(
    target: Path | None = typer.Option(
        None,
        help="Target directory (defaults to ~/.claude)",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files"),
    skills_only: bool = typer.Option(
        False, "--skills-only", help="Install only skills, not agents"
    ),
    agents_only: bool = typer.Option(
        False, "--agents-only", help="Install only agents, not skills"
    ),
    teams_only: bool = typer.Option(
        False, "--teams-only", help="Install only team templates"
    ),
    hooks_only: bool = typer.Option(
        False, "--hooks-only", help="Install only hook templates"
    ),
    no_teams: bool = typer.Option(
        False, "--no-teams", help="Skip installing team templates"
    ),
    no_hooks: bool = typer.Option(
        False, "--no-hooks", help="Skip installing hook templates"
    ),
    list_available: bool = typer.Option(
        False, "--list", help="List available scaffold assets (no install)"
    ),
) -> None:
    """Bootstrap Claude Code scaffold assets to user-level directory.

    Copies the bundled RLM skills, agents, teams, and hooks from the installed
    fleet-rlm
    package to ~/.claude/ (or a custom target), making them available across
    all projects.

    By default, installs skills, agents, teams, and hooks to ~/.claude/. Use
    --*-only flags to install just one category, or --no-* flags to skip
    teams/hooks in a full install.

    Team templates target Claude Code agent teams, which require setting
    CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 in Claude settings/environment.

    Examples:
        $ fleet-rlm init                    # install to ~/.claude/
        $ fleet-rlm init --force            # overwrite existing
        $ fleet-rlm init --list             # show what's available
        $ fleet-rlm init --skills-only      # just skills
        $ fleet-rlm init --teams-only       # just team templates
        $ fleet-rlm init --hooks-only       # just hook templates
        $ fleet-rlm init --no-hooks         # install all except hooks
        $ fleet-rlm init --target /tmp/test # custom location
    """
    try:
        # Default to ~/.claude if no target specified
        if target is None:
            target = Path.home() / ".claude"

        # List mode: just show what's available
        if list_available:
            typer.echo("Available Skills:")
            for skill in scaffold.list_skills():
                typer.echo(
                    f"  - {skill['name']}: {skill['description']} ({skill['files']} files)"
                )
            typer.echo("\nAvailable Agents:")
            for agent in scaffold.list_agents():
                typer.echo(
                    f"  - {agent['name']}: {agent['description']} "
                    f"(model: {agent['model']})"
                )
            typer.echo("\nAvailable Teams:")
            for team in scaffold.list_teams():
                typer.echo(
                    f"  - {team['name']}: {team['description']} ({team['files']} files)"
                )
            typer.echo("\nAvailable Hooks:")
            for hook in scaffold.list_hooks():
                event = f", event: {hook['event']}" if hook["event"] else ""
                typer.echo(f"  - {hook['name']}: {hook['description']}{event}")
            return

        # Install mode
        only_modes = [
            ("skills", skills_only),
            ("agents", agents_only),
            ("teams", teams_only),
            ("hooks", hooks_only),
        ]
        active_only_modes = [name for name, enabled in only_modes if enabled]

        if len(active_only_modes) > 1:
            typer.echo(
                "Error: Only one --*-only mode can be specified at a time.",
                err=True,
            )
            raise typer.Exit(code=1)

        if active_only_modes and (no_teams or no_hooks):
            typer.echo(
                "Error: --*-only modes cannot be combined with --no-teams/--no-hooks.",
                err=True,
            )
            raise typer.Exit(code=1)

        if agents_only:
            installed = scaffold.install_agents(target, force=force)
            total = scaffold.list_agents()
            typer.echo(
                f"Installed {len(installed)} of {len(total)} agents to {target}/agents/"
            )
            if installed:
                typer.echo(f"  Agents: {', '.join(installed)}")
            if len(installed) < len(total):
                skipped = len(total) - len(installed)
                typer.echo(f"  Skipped {skipped} existing (use --force to overwrite)")
        elif skills_only:
            installed = scaffold.install_skills(target, force=force)
            total = scaffold.list_skills()
            typer.echo(
                f"Installed {len(installed)} of {len(total)} skills to {target}/skills/"
            )
            if installed:
                typer.echo(f"  Skills: {', '.join(installed)}")
            if len(installed) < len(total):
                skipped = len(total) - len(installed)
                typer.echo(f"  Skipped {skipped} existing (use --force to overwrite)")
        elif teams_only:
            installed = scaffold.install_teams(target, force=force)
            total = scaffold.list_teams()
            typer.echo(
                f"Installed {len(installed)} of {len(total)} teams to {target}/teams/"
            )
            if installed:
                typer.echo(f"  Teams: {', '.join(installed)}")
            if len(installed) < len(total):
                skipped = len(total) - len(installed)
                typer.echo(f"  Skipped {skipped} existing (use --force to overwrite)")
        elif hooks_only:
            installed = scaffold.install_hooks(target, force=force)
            total = scaffold.list_hooks()
            typer.echo(
                f"Installed {len(installed)} of {len(total)} hooks to {target}/hooks/"
            )
            if installed:
                typer.echo(f"  Hooks: {', '.join(installed)}")
            if len(installed) < len(total):
                skipped = len(total) - len(installed)
                typer.echo(f"  Skipped {skipped} existing (use --force to overwrite)")
        else:
            # Install all categories (with optional exclusions).
            result = scaffold.install_all(
                target,
                force=force,
                include_teams=not no_teams,
                include_hooks=not no_hooks,
            )

            summary_parts = [
                f"{len(result['skills_installed'])} of {result['skills_total']} skills",
                f"{len(result['agents_installed'])} of {result['agents_total']} agents",
            ]
            if not no_teams:
                summary_parts.append(
                    f"{len(result['teams_installed'])} of {result['teams_total']} teams"
                )
            if not no_hooks:
                summary_parts.append(
                    f"{len(result['hooks_installed'])} of {result['hooks_total']} hooks"
                )

            typer.echo(f"Installed {', '.join(summary_parts)} to {target}/")
            if result["skills_installed"]:
                typer.echo(f"  Skills: {', '.join(result['skills_installed'])}")
            if result["agents_installed"]:
                typer.echo(f"  Agents: {', '.join(result['agents_installed'])}")
            if not no_teams and result["teams_installed"]:
                typer.echo(f"  Teams: {', '.join(result['teams_installed'])}")
            if not no_hooks and result["hooks_installed"]:
                typer.echo(f"  Hooks: {', '.join(result['hooks_installed'])}")

            total_skipped = (
                result["skills_total"]
                - len(result["skills_installed"])
                + result["agents_total"]
                - len(result["agents_installed"])
            )
            if not no_teams:
                total_skipped += result["teams_total"] - len(result["teams_installed"])
            if not no_hooks:
                total_skipped += result["hooks_total"] - len(result["hooks_installed"])
            if total_skipped > 0:
                typer.echo(
                    f"  Skipped {total_skipped} existing (use --force to overwrite)"
                )

    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        typer.echo(
            "\nThe scaffold directory was not found. This suggests the fleet-rlm "
            "package is not properly installed or the _scaffold/ data is missing.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        _handle_error(exc)


if __name__ == "__main__":
    app()
