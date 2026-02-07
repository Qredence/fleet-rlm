from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from . import runners

app = typer.Typer(help="Run DSPy RLM demos backed by a Modal sandbox.")


def _print_result(result: dict[str, Any], *, verbose: bool) -> None:
    if verbose:
        typer.echo(json.dumps(result, indent=2, sort_keys=True))
        return

    for key, value in result.items():
        if isinstance(value, (dict, list)):
            typer.echo(f"{key}: {json.dumps(value)}")
        else:
            typer.echo(f"{key}: {value}")


def _handle_error(exc: Exception) -> None:
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
    full_output: bool = typer.Option(
        False, "--full-output", help="Print full JSON output"
    ),
) -> None:
    try:
        result = runners.run_basic(
            question=question,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
            timeout=timeout,
            secret_name=secret_name,
        )
        _print_result(result, verbose=full_output)
    except Exception as exc:
        _handle_error(exc)


@app.command("run-architecture")
def run_architecture(
    docs_path: Path = typer.Option(
        Path("rlm_content/dspy-knowledge/dspy-doc.txt"),
        help="Path to long-context docs text",
    ),
    query: str = typer.Option(..., help="Extraction query"),
    max_iterations: int = typer.Option(25, help="RLM max_iterations"),
    max_llm_calls: int = typer.Option(50, help="RLM max_llm_calls"),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Enable verbose RLM execution"
    ),
    timeout: int = typer.Option(600, help="Modal sandbox timeout in seconds"),
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    full_output: bool = typer.Option(
        False, "--full-output", help="Print full JSON output"
    ),
) -> None:
    try:
        result = runners.run_architecture(
            docs_path=docs_path,
            query=query,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
            timeout=timeout,
            secret_name=secret_name,
        )
        _print_result(result, verbose=full_output)
    except Exception as exc:
        _handle_error(exc)


@app.command("run-api-endpoints")
def run_api_endpoints(
    docs_path: Path = typer.Option(
        Path("rlm_content/dspy-knowledge/dspy-doc.txt"),
        help="Path to long-context docs text",
    ),
    max_iterations: int = typer.Option(20, help="RLM max_iterations"),
    max_llm_calls: int = typer.Option(30, help="RLM max_llm_calls"),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Enable verbose RLM execution"
    ),
    timeout: int = typer.Option(600, help="Modal sandbox timeout in seconds"),
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    full_output: bool = typer.Option(
        False, "--full-output", help="Print full JSON output"
    ),
) -> None:
    try:
        result = runners.run_api_endpoints(
            docs_path=docs_path,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
            timeout=timeout,
            secret_name=secret_name,
        )
        _print_result(result, verbose=full_output)
    except Exception as exc:
        _handle_error(exc)


@app.command("run-error-patterns")
def run_error_patterns(
    docs_path: Path = typer.Option(
        Path("rlm_content/dspy-knowledge/dspy-doc.txt"),
        help="Path to long-context docs text",
    ),
    max_iterations: int = typer.Option(30, help="RLM max_iterations"),
    max_llm_calls: int = typer.Option(40, help="RLM max_llm_calls"),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Enable verbose RLM execution"
    ),
    timeout: int = typer.Option(600, help="Modal sandbox timeout in seconds"),
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    full_output: bool = typer.Option(
        False, "--full-output", help="Print full JSON output"
    ),
) -> None:
    try:
        result = runners.run_error_patterns(
            docs_path=docs_path,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
            timeout=timeout,
            secret_name=secret_name,
        )
        _print_result(result, verbose=full_output)
    except Exception as exc:
        _handle_error(exc)


@app.command("run-trajectory")
def run_trajectory(
    docs_path: Path = typer.Option(
        Path("rlm_content/dspy-knowledge/dspy-doc.txt"),
        help="Path to long-context docs text",
    ),
    chars: int = typer.Option(3000, help="Number of document characters for sample"),
    max_iterations: int = typer.Option(10, help="RLM max_iterations"),
    max_llm_calls: int = typer.Option(10, help="RLM max_llm_calls"),
    verbose: bool = typer.Option(
        False, "--verbose/--no-verbose", help="Enable verbose RLM execution"
    ),
    timeout: int = typer.Option(600, help="Modal sandbox timeout in seconds"),
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    full_output: bool = typer.Option(
        False, "--full-output", help="Print full JSON output"
    ),
) -> None:
    try:
        result = runners.run_trajectory(
            docs_path=docs_path,
            chars=chars,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
            timeout=timeout,
            secret_name=secret_name,
        )
        _print_result(result, verbose=full_output)
    except Exception as exc:
        _handle_error(exc)


@app.command("run-custom-tool")
def run_custom_tool(
    docs_path: Path = typer.Option(
        Path("rlm_content/dspy-knowledge/dspy-doc.txt"),
        help="Path to long-context docs text",
    ),
    chars: int = typer.Option(10000, help="Number of document characters for sample"),
    max_iterations: int = typer.Option(15, help="RLM max_iterations"),
    max_llm_calls: int = typer.Option(20, help="RLM max_llm_calls"),
    verbose: bool = typer.Option(
        True, "--verbose/--no-verbose", help="Enable verbose RLM execution"
    ),
    timeout: int = typer.Option(600, help="Modal sandbox timeout in seconds"),
    secret_name: str = typer.Option("LITELLM", help="Modal secret name"),
    full_output: bool = typer.Option(
        False, "--full-output", help="Print full JSON output"
    ),
) -> None:
    try:
        result = runners.run_custom_tool(
            docs_path=docs_path,
            chars=chars,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
            timeout=timeout,
            secret_name=secret_name,
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
    try:
        result = runners.check_secret_key(secret_name=secret_name, key=key)
        _print_result(result, verbose=full_output)
    except Exception as exc:
        _handle_error(exc)


if __name__ == "__main__":
    app()
