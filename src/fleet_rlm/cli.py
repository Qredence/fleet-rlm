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
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(code=1) from exc


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
    list_available: bool = typer.Option(
        False, "--list", help="List available skills and agents (no install)"
    ),
) -> None:
    """Bootstrap Claude Code skills and agents to user-level directory.

    Copies the bundled RLM skills and agents from the installed fleet-rlm
    package to ~/.claude/ (or a custom target), making them available across
    all projects.

    By default, installs both skills and agents to ~/.claude/. Use --skills-only
    or --agents-only to install just one category.

    Examples:
        $ fleet-rlm init                    # install to ~/.claude/
        $ fleet-rlm init --force            # overwrite existing
        $ fleet-rlm init --list             # show what's available
        $ fleet-rlm init --skills-only      # just skills
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
                    f"  - {agent['name']}: {agent['description']} (model: {agent['model']})"
                )
            return

        # Install mode
        if agents_only and skills_only:
            typer.echo(
                "Error: Cannot specify both --skills-only and --agents-only", err=True
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
        else:
            # Install both
            result = scaffold.install_all(target, force=force)
            typer.echo(
                f"Installed {len(result['skills_installed'])} of {result['skills_total']} skills "
                f"and {len(result['agents_installed'])} of {result['agents_total']} agents to {target}/"
            )
            if result["skills_installed"]:
                typer.echo(f"  Skills: {', '.join(result['skills_installed'])}")
            if result["agents_installed"]:
                typer.echo(f"  Agents: {', '.join(result['agents_installed'])}")
            total_skipped = (
                result["skills_total"]
                - len(result["skills_installed"])
                + result["agents_total"]
                - len(result["agents_installed"])
            )
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
