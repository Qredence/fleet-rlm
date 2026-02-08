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
    docs_path: Path = typer.Option(
        ...,
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


if __name__ == "__main__":
    app()
