"""Demo runner functions for RLM demonstrations.

Each runner instantiates a ModalInterpreter, executes a DSPy RLM
pipeline, and returns the structured result.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import dspy
import modal

from .runners import (
    _interpreter,
    _read_docs,
    _require_planner_ready,
    _rlm_trajectory_payload,
)
from .signatures import (
    AnalyzeLongDocument,
    ExtractAPIEndpoints,
    ExtractArchitecture,
    ExtractWithCustomTool,
    FindErrorPatterns,
    SummarizeLongDocument,
)
from .utils.tools import regex_extract

logger = logging.getLogger(__name__)


def run_basic(
    *,
    question: str,
    max_iterations: int = 15,
    max_llm_calls: int = 30,
    verbose: bool = True,
    timeout: int = 600,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    include_trajectory: bool = True,
    env_file: Path | None = None,
) -> dict[str, Any]:
    """Run a basic RLM question-answering task.

    Executes a simple question-answering task using the RLM with a Modal
    sandbox interpreter.

    Args:
        question: The question to answer.
        max_iterations: Maximum RLM iterations (default: 15).
        max_llm_calls: Maximum LLM calls (default: 30).
        verbose: Enable verbose output (default: True).
        timeout: Sandbox timeout in seconds (default: 600).
        secret_name: Modal secret name (default: "LITELLM").
        volume_name: Optional Modal volume name.
        include_trajectory: Include RLM trajectory metadata in output.
        env_file: Optional path to .env file.

    Returns:
        Dictionary containing:
            - answer: The RLM's answer to the question
            - trajectory_steps / trajectory / final_reasoning (when included)
    """
    _require_planner_ready(env_file)

    interpreter = _interpreter(
        timeout=timeout, secret_name=secret_name, volume_name=volume_name
    )
    rlm = dspy.RLM(
        signature="question -> answer",
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
    )

    try:
        result = rlm(question=question)
        response = {"answer": result.answer}
        response.update(
            _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
        )
        return response
    finally:
        interpreter.shutdown()


def run_architecture(
    *,
    docs_path: Path | str,
    query: str,
    max_iterations: int = 25,
    max_llm_calls: int = 50,
    verbose: bool = True,
    timeout: int = 600,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    include_trajectory: bool = True,
    env_file: Path | None = None,
) -> dict[str, Any]:
    """Extract DSPy architecture information from documentation.

    Uses the ExtractArchitecture signature to analyze documentation and
    extract modules, optimizers, and design principles.

    Args:
        docs_path: Path to documentation file.
        query: The extraction query (e.g., "What are the main components?").
        max_iterations: Maximum RLM iterations (default: 25).
        max_llm_calls: Maximum LLM calls (default: 50).
        verbose: Enable verbose output (default: True).
        timeout: Sandbox timeout in seconds (default: 600).
        secret_name: Modal secret name (default: "LITELLM").
        volume_name: Optional Modal volume name.
        include_trajectory: Include RLM trajectory metadata in output.
        env_file: Optional path to .env file.

    Returns:
        Dictionary containing:
            - modules: List of DSPy modules identified
            - optimizers: List of optimizers found
            - design_principles: Key design principles text
            - doc_chars: Character count of processed document
            - doc_lines: Line count of processed document
    """
    docs = _read_docs(docs_path)
    _require_planner_ready(env_file)

    interpreter = _interpreter(
        timeout=timeout, secret_name=secret_name, volume_name=volume_name
    )
    rlm = dspy.RLM(
        signature=ExtractArchitecture,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
    )

    try:
        result = rlm(docs=docs, query=query)
        response = {
            "modules": result.modules,
            "optimizers": result.optimizers,
            "design_principles": result.design_principles,
            "doc_chars": len(docs),
            "doc_lines": len(docs.splitlines()),
        }
        response.update(
            _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
        )
        return response
    finally:
        interpreter.shutdown()


def run_api_endpoints(
    *,
    docs_path: Path | str,
    max_iterations: int = 20,
    max_llm_calls: int = 30,
    verbose: bool = True,
    timeout: int = 600,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    include_trajectory: bool = True,
    env_file: Path | None = None,
) -> dict[str, Any]:
    """Extract API endpoints from documentation.

    Uses the ExtractAPIEndpoints signature to scan documentation for API
    endpoint definitions and their parameters.

    Args:
        docs_path: Path to documentation file.
        max_iterations: Maximum RLM iterations (default: 20).
        max_llm_calls: Maximum LLM calls (default: 30).
        verbose: Enable verbose output (default: True).
        timeout: Sandbox timeout in seconds (default: 600).
        secret_name: Modal secret name (default: "LITELLM").
        volume_name: Optional Modal volume name.
        include_trajectory: Include RLM trajectory metadata in output.
        env_file: Optional path to .env file.

    Returns:
        Dictionary containing:
            - api_endpoints: List of API endpoint descriptions
            - count: Number of endpoints found
    """
    docs = _read_docs(docs_path)
    _require_planner_ready(env_file)

    interpreter = _interpreter(
        timeout=timeout, secret_name=secret_name, volume_name=volume_name
    )
    rlm = dspy.RLM(
        signature=ExtractAPIEndpoints,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
    )

    try:
        result = rlm(docs=docs)
        response = {
            "api_endpoints": result.api_endpoints,
            "count": len(result.api_endpoints),
        }
        response.update(
            _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
        )
        return response
    finally:
        interpreter.shutdown()


def run_error_patterns(
    *,
    docs_path: Path | str,
    max_iterations: int = 30,
    max_llm_calls: int = 40,
    verbose: bool = True,
    timeout: int = 600,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    include_trajectory: bool = True,
    env_file: Path | None = None,
) -> dict[str, Any]:
    """Find and categorize error patterns in documentation.

    Uses the FindErrorPatterns signature to identify common errors,
    their causes, and solutions from documentation.

    Args:
        docs_path: Path to documentation file.
        max_iterations: Maximum RLM iterations (default: 30).
        max_llm_calls: Maximum LLM calls (default: 40).
        verbose: Enable verbose output (default: True).
        timeout: Sandbox timeout in seconds (default: 600).
        secret_name: Modal secret name (default: "LITELLM").
        volume_name: Optional Modal volume name.
        include_trajectory: Include RLM trajectory metadata in output.
        env_file: Optional path to .env file.

    Returns:
        Dictionary containing:
            - error_categories: Dict mapping error types to solutions
            - total_errors_found: Total count of identified errors
    """
    docs = _read_docs(docs_path)
    _require_planner_ready(env_file)

    interpreter = _interpreter(
        timeout=timeout, secret_name=secret_name, volume_name=volume_name
    )
    rlm = dspy.RLM(
        signature=FindErrorPatterns,
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
    )

    try:
        result = rlm(docs=docs)
        response = {
            "error_categories": result.error_categories,
            "total_errors_found": result.total_errors_found,
        }
        response.update(
            _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
        )
        return response
    finally:
        interpreter.shutdown()


def run_trajectory(
    *,
    docs_path: Path | str,
    chars: int = 3000,
    max_iterations: int = 10,
    max_llm_calls: int = 10,
    verbose: bool = False,
    timeout: int = 600,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    env_file: Path | None = None,
) -> dict[str, Any]:
    """Run RLM with trajectory tracking for debugging/analysis.

    Executes a text summarization task on a sample of documentation
    while capturing the full reasoning trajectory for inspection.

    Args:
        docs_path: Path to documentation file.
        chars: Number of characters to sample from docs (default: 3000).
        max_iterations: Maximum RLM iterations (default: 10).
        max_llm_calls: Maximum LLM calls (default: 10).
        verbose: Enable verbose output (default: False).
        timeout: Sandbox timeout in seconds (default: 600).
        secret_name: Modal secret name (default: "LITELLM").
        volume_name: Optional Modal volume name.
        include_trajectory: Include RLM trajectory metadata in output.
        env_file: Optional path to .env file.

    Returns:
        Dictionary containing:
            - summary: Generated summary of the text sample
            - trajectory_steps: Number of steps taken
            - trajectory: List of step details (reasoning, code snippets)
    """
    docs = _read_docs(docs_path)
    _require_planner_ready(env_file)

    interpreter = _interpreter(
        timeout=timeout, secret_name=secret_name, volume_name=volume_name
    )
    rlm = dspy.RLM(
        signature="text -> summary",
        interpreter=interpreter,
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
    )

    try:
        sample = docs[:chars]
        result = rlm(text=sample)
        trajectory = []
        for idx, step in enumerate(getattr(result, "trajectory", []), start=1):
            trajectory.append(
                {
                    "step": idx,
                    "reasoning": str(step.get("reasoning", "N/A"))[:100],
                    "code": str(step.get("code", ""))[:60],
                }
            )
        return {
            "summary": result.summary,
            "trajectory_steps": len(trajectory),
            "trajectory": trajectory,
        }
    finally:
        interpreter.shutdown()


def run_custom_tool(
    *,
    docs_path: Path | str,
    chars: int = 10000,
    max_iterations: int = 15,
    max_llm_calls: int = 20,
    verbose: bool = True,
    timeout: int = 600,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    include_trajectory: bool = True,
    env_file: Path | None = None,
) -> dict[str, Any]:
    """Run RLM with custom regex tool for structured extraction.

    Uses the ExtractWithCustomTool signature which can invoke the
    regex_extract tool to find markdown headers and code blocks.

    Args:
        docs_path: Path to documentation file.
        chars: Number of characters to process (default: 10000).
        max_iterations: Maximum RLM iterations (default: 15).
        max_llm_calls: Maximum LLM calls (default: 20).
        verbose: Enable verbose output (default: True).
        timeout: Sandbox timeout in seconds (default: 600).
        secret_name: Modal secret name (default: "LITELLM").
        volume_name: Optional Modal volume name.
        env_file: Optional path to .env file.

    Returns:
        Dictionary containing:
            - headers: List of markdown headers found
            - code_blocks: List of code blocks found
            - structure_summary: Summary of document structure
            - headers_count: Number of headers found
            - code_blocks_count: Number of code blocks found
    """
    docs = _read_docs(docs_path)
    _require_planner_ready(env_file)

    interpreter = _interpreter(
        timeout=timeout, secret_name=secret_name, volume_name=volume_name
    )
    rlm = dspy.RLM(
        signature=ExtractWithCustomTool,
        interpreter=interpreter,
        tools=[regex_extract],
        max_iterations=max_iterations,
        max_llm_calls=max_llm_calls,
        verbose=verbose,
    )

    try:
        result = rlm(docs=docs[:chars])
        response = {
            "headers": result.headers,
            "code_blocks": result.code_blocks,
            "structure_summary": result.structure_summary,
            "headers_count": len(result.headers),
            "code_blocks_count": len(result.code_blocks),
        }
        response.update(
            _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
        )
        return response
    finally:
        interpreter.shutdown()


def run_long_context(
    *,
    docs_path: Path | str,
    query: str,
    mode: str = "analyze",
    max_iterations: int = 30,
    max_llm_calls: int = 50,
    verbose: bool = True,
    timeout: int = 900,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    include_trajectory: bool = True,
    env_file: Path | None = None,
) -> dict[str, Any]:
    """Run a long-context analysis or summarization task.

    Loads a document into the sandbox and uses injected helpers
    (``peek``, ``grep``, ``chunk_by_size``, ``chunk_by_headers``,
    buffers, volume persistence) to let the RLM explore it
    programmatically.

    Args:
        docs_path: Path to the document file.
        query: The analysis query or focus topic.
        mode: ``"analyze"`` (default) or ``"summarize"``.
        max_iterations: Maximum RLM iterations (default: 30).
        max_llm_calls: Maximum LLM calls (default: 50).
        verbose: Enable verbose output (default: True).
        timeout: Sandbox timeout in seconds (default: 900).
        secret_name: Modal secret name (default: "LITELLM").
        volume_name: Optional Modal volume name for persistence.
        include_trajectory: Include RLM trajectory metadata in output.
        env_file: Optional path to .env file.

    Returns:
        Dictionary with results specific to the chosen mode.
        For ``"analyze"``:
            - findings, answer, sections_examined, doc_chars
        For ``"summarize"``:
            - summary, key_points, coverage_pct, doc_chars

    Raises:
        ValueError: If *mode* is not ``"analyze"`` or ``"summarize"``.
    """
    if mode not in ("analyze", "summarize"):
        raise ValueError(f"mode must be 'analyze' or 'summarize', got {mode!r}")

    docs = _read_docs(docs_path)
    _require_planner_ready(env_file)

    sig = AnalyzeLongDocument if mode == "analyze" else SummarizeLongDocument

    with _interpreter(
        timeout=timeout, secret_name=secret_name, volume_name=volume_name
    ) as interpreter:
        rlm = dspy.RLM(
            signature=sig,
            interpreter=interpreter,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
            verbose=verbose,
        )

        if mode == "analyze":
            result = rlm(document=docs, query=query)
            response = {
                "findings": result.findings,
                "answer": result.answer,
                "sections_examined": result.sections_examined,
                "doc_chars": len(docs),
            }
            response.update(
                _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
            )
            return response
        else:
            result = rlm(document=docs, focus=query)
            response = {
                "summary": result.summary,
                "key_points": result.key_points,
                "coverage_pct": result.coverage_pct,
                "doc_chars": len(docs),
            }
            response.update(
                _rlm_trajectory_payload(result, include_trajectory=include_trajectory)
            )
            return response


def check_secret_presence(*, secret_name: str = "LITELLM") -> dict[str, bool]:
    """Check which DSPy environment variables are present in a Modal secret.

    Creates a temporary sandbox with the specified secret and checks
    for the presence of DSPy-related environment variables.

    Args:
        secret_name: Name of the Modal secret to check (default: "LITELLM").

    Returns:
        Dictionary mapping environment variable names to boolean presence.
        Checks: DSPY_LM_MODEL, DSPY_LM_API_BASE, DSPY_LLM_API_KEY, DSPY_LM_MAX_TOKENS.
    """
    app = modal.App.lookup("dspy-rlm-secret-check", create_if_missing=True)
    sb = modal.Sandbox.create(app=app, secrets=[modal.Secret.from_name(secret_name)])
    try:
        code = r"""
import json, os
keys = [
  "DSPY_LM_MODEL",
  "DSPY_LM_API_BASE",
  "DSPY_LLM_API_KEY",
  "DSPY_LM_MAX_TOKENS",
]
print(json.dumps({k: bool(os.environ.get(k)) for k in keys}))
"""
        proc = sb.exec("python", "-c", code, timeout=60)
        proc.wait()
        return json.loads(proc.stdout.read().strip())
    finally:
        sb.terminate()


def check_secret_key(
    *, secret_name: str = "LITELLM", key: str = "DSPY_LLM_API_KEY"
) -> dict[str, Any]:
    """Check a specific environment variable in a Modal secret.

    Creates a temporary sandbox to verify that a specific key exists
    in the Modal secret, without exposing the actual value.

    Args:
        secret_name: Name of the Modal secret to check (default: "LITELLM").
        key: Environment variable name to check (default: "DSPY_LLM_API_KEY").

    Returns:
        Dictionary containing:
            - present: Boolean indicating if the key exists and is non-empty
            - length: Character length of the key's value (0 if not present)
    """
    app = modal.App.lookup("dspy-rlm-secret-check", create_if_missing=True)
    sb = modal.Sandbox.create(app=app, secrets=[modal.Secret.from_name(secret_name)])
    try:
        code = (
            "import json, os\n"
            f"val=os.environ.get({key!r}, '')\n"
            "print(json.dumps({'present': bool(val), 'length': len(val)}))\n"
        )
        proc = sb.exec("python", "-c", code, timeout=60)
        proc.wait()
        return json.loads(proc.stdout.read().strip())
    finally:
        sb.terminate()
