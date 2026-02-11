"""Command-line interface for DSPy RLM with Modal.

This module provides a Typer-based CLI for running RLM demonstrations
and diagnostics. Commands are organized by use case:

Demo commands:
    - run-basic: Simple question-answering with RLM
    - run-architecture: Extract DSPy architecture from documentation
    - run-api-endpoints: Extract API endpoints from documentation
    - run-error-patterns: Find error patterns in documentation
    - run-trajectory: Run with trajectory tracking
    - run-custom-tool: Run with custom regex tool

Diagnostic commands:
    - check-secret: Verify Modal secret configuration
    - check-secret-key: Check specific secret key presence

Usage:
    $ python -m fleet_rlm.cli run-basic --question "What is DSPy?"
    $ python -m fleet_rlm.cli check-secret
"""

from __future__ import annotations

import json
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import typer

from . import runners
from . import scaffold

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


def _code_chat_missing_extras_message(missing: list[str]) -> str:
    pkg_list = ", ".join(sorted(missing))
    return (
        "Interactive code-chat dependencies are missing: "
        f"{pkg_list}\n"
        "Install them with:\n"
        "  uv sync --extra dev --extra interactive"
    )


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
    no_stream: bool,
    profile: str,
) -> None:
    from .interactive import check_interactive_dependencies
    from .interactive.config import get_profile
    from .interactive.models import SessionConfig
    from .interactive.session import CodeChatSession

    dep_check = check_interactive_dependencies()
    if not dep_check.ok:
        typer.echo(_code_chat_missing_extras_message(dep_check.missing), err=True)
        raise typer.Exit(code=2)

    profile_cfg = get_profile(profile)
    session_cfg = SessionConfig(
        profile_name=profile_cfg.name,
        docs_path=str(docs_path) if docs_path else profile_cfg.docs_path,
        secret_name=secret_name or profile_cfg.secret_name,
        volume_name=volume_name if volume_name is not None else profile_cfg.volume_name,
        timeout=timeout or profile_cfg.timeout,
        react_max_iters=react_max_iters or profile_cfg.react_max_iters,
        rlm_max_iterations=rlm_max_iterations or profile_cfg.rlm_max_iterations,
        rlm_max_llm_calls=rlm_max_llm_calls or profile_cfg.rlm_max_llm_calls,
        trace=trace if trace is not None else profile_cfg.trace,
        stream=(not no_stream) if no_stream else profile_cfg.stream,
    )

    with runners.build_react_chat_agent(
        docs_path=Path(session_cfg.docs_path) if session_cfg.docs_path else None,
        react_max_iters=session_cfg.react_max_iters,
        rlm_max_iterations=session_cfg.rlm_max_iterations,
        rlm_max_llm_calls=session_cfg.rlm_max_llm_calls,
        timeout=session_cfg.timeout,
        secret_name=session_cfg.secret_name,
        volume_name=session_cfg.volume_name,
    ) as chat_agent:
        session = CodeChatSession(agent=chat_agent, config=session_cfg)
        session.run()


@app.command("run-basic")
def run_basic(
    question: str = typer.Option(..., help="Question to answer via RLM"),
    max_iterations: int = typer.Option(15, help="RLM max_iterations"),
    max_llm_calls: int = typer.Option(30, help="RLM max_llm_calls"),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Enable verbose RLM execution"
    ),
    timeout: int = typer.Option(600, help="Modal sandbox timeout in seconds"),
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    volume_name: str | None = typer.Option(
        None, help="Modal volume name for persistent storage"
    ),
    full_output: bool = typer.Option(
        False, "--full-output", help="Print full JSON output"
    ),
) -> None:
    """Run a basic RLM question-answering demo.

    Executes a simple question-answer task using the RLM with a Modal sandbox.
    The RLM will reason through the question and provide an answer.

    Example:
        $ python -m fleet_rlm.cli run-basic \\
            --question "What is DSPy?" \\
            --max-iterations 10
    """
    try:
        result = runners.run_basic(
            question=question,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
            timeout=timeout,
            secret_name=secret_name,
            volume_name=volume_name,
        )
        _print_result(result, verbose=full_output)
    except Exception as exc:
        _handle_error(exc)


@app.command("run-architecture")
def run_architecture(
    docs_path: Path | None = typer.Option(
        None,
        "--docs-path",
        help="Path to long-context docs text (required)",
    ),
    query: str = typer.Option(..., help="Extraction query"),
    max_iterations: int = typer.Option(25, help="RLM max_iterations"),
    max_llm_calls: int = typer.Option(50, help="RLM max_llm_calls"),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Enable verbose RLM execution"
    ),
    timeout: int = typer.Option(600, help="Modal sandbox timeout in seconds"),
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    volume_name: str | None = typer.Option(
        None, help="Modal volume name for persistent storage"
    ),
    full_output: bool = typer.Option(
        False, "--full-output", help="Print full JSON output"
    ),
) -> None:
    """Extract DSPy architecture information from documentation.

    Uses the ExtractArchitecture signature to analyze documentation and
    extract modules, optimizers, and design principles.

    Example:
        $ python -m fleet_rlm.cli run-architecture \\
            --docs-path docs.txt \\
            --query "What are the main components?"
    """
    if docs_path is None:
        typer.echo("Error: '--docs-path' is required for run-architecture.", err=True)
        raise typer.Exit(code=2)
    try:
        result = runners.run_architecture(
            docs_path=docs_path,
            query=query,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
            timeout=timeout,
            secret_name=secret_name,
            volume_name=volume_name,
        )
        _print_result(result, verbose=full_output)
    except Exception as exc:
        _handle_error(exc)


@app.command("run-api-endpoints")
def run_api_endpoints(
    docs_path: Path = typer.Option(
        ...,
        "--docs-path",
        help="Path to long-context docs text (required)",
    ),
    max_iterations: int = typer.Option(20, help="RLM max_iterations"),
    max_llm_calls: int = typer.Option(30, help="RLM max_llm_calls"),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Enable verbose RLM execution"
    ),
    timeout: int = typer.Option(600, help="Modal sandbox timeout in seconds"),
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    volume_name: str | None = typer.Option(
        None, help="Modal volume name for persistent storage"
    ),
    full_output: bool = typer.Option(
        False, "--full-output", help="Print full JSON output"
    ),
) -> None:
    """Extract API endpoints from documentation.

    Uses the ExtractAPIEndpoints signature to scan documentation and
    catalog API endpoints with their parameters and details.

    Example:
        $ python -m fleet_rlm.cli run-api-endpoints --docs-path api-docs.txt
    """
    try:
        result = runners.run_api_endpoints(
            docs_path=docs_path,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
            timeout=timeout,
            secret_name=secret_name,
            volume_name=volume_name,
        )
        _print_result(result, verbose=full_output)
    except Exception as exc:
        _handle_error(exc)


@app.command("run-error-patterns")
def run_error_patterns(
    docs_path: Path = typer.Option(
        ...,
        "--docs-path",
        help="Path to long-context docs text (required)",
    ),
    max_iterations: int = typer.Option(30, help="RLM max_iterations"),
    max_llm_calls: int = typer.Option(40, help="RLM max_llm_calls"),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Enable verbose RLM execution"
    ),
    timeout: int = typer.Option(600, help="Modal sandbox timeout in seconds"),
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    volume_name: str | None = typer.Option(
        None, help="Modal volume name for persistent storage"
    ),
    full_output: bool = typer.Option(
        False, "--full-output", help="Print full JSON output"
    ),
) -> None:
    """Find and categorize error patterns in documentation.

    Uses the FindErrorPatterns signature to analyze documentation for
    common errors, their causes, and solutions.

    Example:
        $ python -m fleet_rlm.cli run-error-patterns --docs-path errors.txt
    """
    try:
        result = runners.run_error_patterns(
            docs_path=docs_path,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
            timeout=timeout,
            secret_name=secret_name,
            volume_name=volume_name,
        )
        _print_result(result, verbose=full_output)
    except Exception as exc:
        _handle_error(exc)


@app.command("run-trajectory")
def run_trajectory(
    docs_path: Path = typer.Option(
        ...,
        "--docs-path",
        help="Path to long-context docs text (required)",
    ),
    chars: int = typer.Option(3000, help="Number of document characters for sample"),
    max_iterations: int = typer.Option(10, help="RLM max_iterations"),
    max_llm_calls: int = typer.Option(10, help="RLM max_llm_calls"),
    verbose: bool = typer.Option(
        False, "--verbose/--no-verbose", help="Enable verbose RLM execution"
    ),
    timeout: int = typer.Option(600, help="Modal sandbox timeout in seconds"),
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    volume_name: str | None = typer.Option(
        None, help="Modal volume name for persistent storage"
    ),
    full_output: bool = typer.Option(
        False, "--full-output", help="Print full JSON output"
    ),
) -> None:
    """Run RLM with trajectory tracking for debugging.

    Executes a text summarization task while capturing the full reasoning
    trajectory (steps taken, code executed) for analysis.

    Example:
        $ python -m fleet_rlm.cli run-trajectory \\
            --chars 5000 \\
            --verbose
    """
    try:
        result = runners.run_trajectory(
            docs_path=docs_path,
            chars=chars,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
            timeout=timeout,
            secret_name=secret_name,
            volume_name=volume_name,
        )
        _print_result(result, verbose=full_output)
    except Exception as exc:
        _handle_error(exc)


@app.command("run-custom-tool")
def run_custom_tool(
    docs_path: Path = typer.Option(
        ...,
        "--docs-path",
        help="Path to long-context docs text (required)",
    ),
    chars: int = typer.Option(10000, help="Number of document characters for sample"),
    max_iterations: int = typer.Option(15, help="RLM max_iterations"),
    max_llm_calls: int = typer.Option(20, help="RLM max_llm_calls"),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Enable verbose RLM execution"
    ),
    timeout: int = typer.Option(600, help="Modal sandbox timeout in seconds"),
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    volume_name: str | None = typer.Option(
        None, help="Modal volume name for persistent storage"
    ),
    full_output: bool = typer.Option(
        False, "--full-output", help="Print full JSON output"
    ),
) -> None:
    """Run RLM with custom regex tool for structured extraction.

    Uses the ExtractWithCustomTool signature which can call regex_extract
    to find markdown headers and code blocks in documentation.

    Example:
        $ python -m fleet_rlm.cli run-custom-tool \\
            --chars 5000 \\
            --max-iterations 20
    """
    try:
        result = runners.run_custom_tool(
            docs_path=docs_path,
            chars=chars,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
            timeout=timeout,
            secret_name=secret_name,
            volume_name=volume_name,
        )
        _print_result(result, verbose=full_output)
    except Exception as exc:
        _handle_error(exc)


@app.command("run-long-context")
def run_long_context(
    docs_path: Path = typer.Option(
        ...,
        "--docs-path",
        help="Path to a long document to process",
    ),
    query: str = typer.Option(..., help="Analysis query or focus topic"),
    mode: str = typer.Option(
        "analyze",
        help="Processing mode: 'analyze' or 'summarize'",
    ),
    max_iterations: int = typer.Option(30, help="RLM max_iterations"),
    max_llm_calls: int = typer.Option(50, help="RLM max_llm_calls"),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Enable verbose RLM execution"
    ),
    timeout: int = typer.Option(900, help="Modal sandbox timeout in seconds"),
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    volume_name: str | None = typer.Option(
        None, help="Modal volume name for persistent storage"
    ),
    full_output: bool = typer.Option(
        False, "--full-output", help="Print full JSON output"
    ),
) -> None:
    """Analyze or summarize a long document using RLM sandbox helpers.

    Loads the document into the Modal sandbox and leverages injected
    helpers (peek, grep, chunk_by_size, chunk_by_headers, buffers) so
    the RLM can explore it programmatically.

    Example:
        $ fleet-rlm run-long-context \\
            --docs-path big-doc.txt \\
            --query "What are the main design decisions?" \\
            --mode analyze
    """
    try:
        result = runners.run_long_context(
            docs_path=docs_path,
            query=query,
            mode=mode,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
            timeout=timeout,
            secret_name=secret_name,
            volume_name=volume_name,
        )
        _print_result(result, verbose=full_output)
    except Exception as exc:
        _handle_error(exc)


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
    no_stream: bool = typer.Option(False, "--no-stream", help="Disable DSPy streaming"),
    profile: str = typer.Option("default", "--profile", help="Interactive profile name"),
) -> None:
    """Start coding-first interactive DSPy ReAct + RLM terminal UI.

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
            no_stream=no_stream,
            profile=profile,
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
    no_stream: bool = typer.Option(False, "--no-stream", help="Disable DSPy streaming"),
    profile: str = typer.Option("default", "--profile", help="Interactive profile name"),
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
            no_stream=no_stream,
            profile=profile,
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
        missing = [
            pkg
            for pkg in ("fastapi", "uvicorn")
            if find_spec(pkg) is None
        ]
        if missing:
            typer.echo(
                "Server dependencies missing: "
                + ", ".join(missing)
                + "\nInstall with:\n  uv sync --extra dev --extra server",
                err=True,
            )
            raise typer.Exit(code=2)

        import uvicorn

        from .server.app import ServerRuntimeConfig, create_app

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
        missing = [
            pkg
            for pkg in ("fastmcp",)
            if find_spec(pkg) is None
        ]
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
            typer.echo("transport must be one of: stdio, sse, streamable-http", err=True)
            raise typer.Exit(code=2)
    except Exception as exc:
        _handle_error(exc)


@app.command("check-secret")
def check_secret(
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    full_output: bool = typer.Option(
        False, "--full-output", help="Print full JSON output"
    ),
) -> None:
    """Check which DSPy environment variables are present in Modal secret.

    Verifies that the required environment variables (DSPY_LM_MODEL,
    DSPY_LM_API_BASE, DSPY_LLM_API_KEY, DSPY_LM_MAX_TOKENS) are available
    in the specified Modal secret.

    Example:
        $ python -m fleet_rlm.cli check-secret --secret-name LITELLM
    """
    try:
        result = runners.check_secret_presence(secret_name=secret_name)
        _print_result(result, verbose=full_output)
    except Exception as exc:
        _handle_error(exc)


@app.command("check-secret-key")
def check_secret_key(
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    key: str = typer.Option(
        "DSPY_LLM_API_KEY", help="Environment variable name to inspect"
    ),
    full_output: bool = typer.Option(
        False, "--full-output", help="Print full JSON output"
    ),
) -> None:
    """Check a specific environment variable in Modal secret.

    Verifies that a specific key exists in the Modal secret and reports
    its presence and length (without exposing the actual value).

    Example:
        $ python -m fleet_rlm.cli check-secret-key \\
            --secret-name LITELLM \\
            --key DSPY_LLM_API_KEY
    """
    try:
        result = runners.check_secret_key(secret_name=secret_name, key=key)
        _print_result(result, verbose=full_output)
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
