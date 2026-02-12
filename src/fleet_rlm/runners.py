"""High-level runner functions for RLM demonstrations.

This module provides convenient functions for running various RLM-based
demonstrations using DSPy signatures and the Modal interpreter. Each runner
function handles the complete lifecycle: configuration, execution, and cleanup.

Runner categories:
    - Basic execution: run_basic
    - Documentation extraction: run_architecture, run_api_endpoints, run_error_patterns
    - Trajectory analysis: run_trajectory
    - Tool usage: run_custom_tool
    - Diagnostics: check_secret_presence, check_secret_key

All runners automatically:
    1. Configure the DSPy planner from environment
    2. Create and start a Modal interpreter
    3. Execute the RLM with appropriate signature
    4. Return structured results
    5. Clean up resources (interpreter shutdown)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import dspy
import modal

from .core.config import configure_planner_from_env
from .core.interpreter import ModalInterpreter
from .react.agent import RLMReActChatAgent
from .signatures import (
    AnalyzeLongDocument,
    ExtractAPIEndpoints,
    ExtractArchitecture,
    ExtractWithCustomTool,
    FindErrorPatterns,
    SummarizeLongDocument,
)
from .utils.tools import regex_extract


def _rlm_trajectory_payload(result: Any, *, include_trajectory: bool) -> dict[str, Any]:
    """Build a normalized trajectory payload from a DSPy RLM result."""
    if not include_trajectory:
        return {}

    trajectory = list(getattr(result, "trajectory", []) or [])
    payload: dict[str, Any] = {
        "trajectory_steps": len(trajectory),
        "trajectory": trajectory,
    }
    final_reasoning = getattr(result, "final_reasoning", None)
    if final_reasoning:
        payload["final_reasoning"] = final_reasoning
    return payload


def _require_planner_ready(env_file: Path | None = None) -> None:
    """Ensure the DSPy planner LM is configured.

    Attempts to configure the planner from environment variables. Raises
    an error if configuration fails and no LM is currently set.

    Args:
        env_file: Optional path to a specific .env file to load.

    Raises:
        RuntimeError: If the planner LM is not configured and cannot be
            configured from environment variables.
    """
    ready = configure_planner_from_env(env_file=env_file)
    if not ready and dspy.settings.lm is None:
        raise RuntimeError(
            "Planner LM not configured. Set DSPY_LM_MODEL and DSPY_LLM_API_KEY (or DSPY_LM_API_KEY)."
        )


def _read_docs(path: Path | str) -> str:
    """Read documentation text from a file.

    Args:
        path: Path to the documentation file (string or Path).

    Returns:
        The file contents as a string.

    Raises:
        FileNotFoundError: If the specified path does not exist.
    """
    docs_path = Path(path)
    if not docs_path.exists():
        raise FileNotFoundError(f"Docs path does not exist: {docs_path}")
    return docs_path.read_text()


def _interpreter(
    *,
    timeout: int = 600,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
) -> ModalInterpreter:
    """Create a ModalInterpreter with the specified configuration.

    Args:
        timeout: Sandbox timeout in seconds (default: 600).
        secret_name: Name of the Modal secret containing API keys.
        volume_name: Optional name of a Modal volume for persistent storage.

    Returns:
        A configured ModalInterpreter instance (not yet started).
    """
    return ModalInterpreter(
        timeout=timeout, secret_name=secret_name, volume_name=volume_name
    )


def build_react_chat_agent(
    *,
    docs_path: Path | str | None = None,
    react_max_iters: int = 10,
    rlm_max_iterations: int = 30,
    rlm_max_llm_calls: int = 50,
    timeout: int = 900,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    verbose: bool = False,
    history_max_turns: int | None = None,
    extra_tools: list | None = None,
    env_file: Path | None = None,
    planner_lm: Any | None = None,
) -> RLMReActChatAgent:
    """Build an interactive DSPy ReAct chat agent for RLM workflows.

    Args:
        docs_path: Optional path to preload as the active document.
        react_max_iters: Maximum DSPy ReAct tool-iteration loops.
        rlm_max_iterations: Maximum iterations for internal ``dspy.RLM`` tools.
        rlm_max_llm_calls: Maximum LLM calls for internal RLM/interpreter usage.
        timeout: Modal sandbox timeout in seconds.
        secret_name: Modal secret name.
        volume_name: Optional Modal volume name for persistence.
        verbose: Verbose mode for internal RLM calls.
        history_max_turns: Optional cap for retained chat turns.
        extra_tools: Optional additional callable tools exposed to ReAct.
        env_file: Optional ``.env`` file path for planner setup.
        planner_lm: Optional pre-configured LM. When provided, skips the
            global ``dspy.configure()`` call, allowing the caller to use
            ``dspy.context()`` for async-safe configuration.

    Returns:
        A configured ``RLMReActChatAgent`` instance.
    """
    if planner_lm is None:
        _require_planner_ready(env_file)

    agent = RLMReActChatAgent(
        react_max_iters=react_max_iters,
        rlm_max_iterations=rlm_max_iterations,
        rlm_max_llm_calls=rlm_max_llm_calls,
        timeout=timeout,
        secret_name=secret_name,
        volume_name=volume_name,
        verbose=verbose,
        history_max_turns=history_max_turns,
        extra_tools=extra_tools,
    )

    if docs_path is not None:
        agent.load_document(str(docs_path), alias="active")

    return agent


def run_react_chat_once(
    *,
    message: str,
    docs_path: Path | str | None = None,
    react_max_iters: int = 10,
    rlm_max_iterations: int = 30,
    rlm_max_llm_calls: int = 50,
    timeout: int = 900,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    verbose: bool = False,
    include_trajectory: bool = True,
    env_file: Path | None = None,
) -> dict[str, Any]:
    """Run a single prompt through the interactive ReAct chat agent."""
    with build_react_chat_agent(
        docs_path=docs_path,
        react_max_iters=react_max_iters,
        rlm_max_iterations=rlm_max_iterations,
        rlm_max_llm_calls=rlm_max_llm_calls,
        timeout=timeout,
        secret_name=secret_name,
        volume_name=volume_name,
        verbose=verbose,
        env_file=env_file,
    ) as agent:
        result = agent.chat_turn(message)
        if not include_trajectory:
            result.pop("trajectory", None)
        return result


async def arun_react_chat_once(
    *,
    message: str,
    docs_path: Path | str | None = None,
    react_max_iters: int = 10,
    rlm_max_iterations: int = 30,
    rlm_max_llm_calls: int = 50,
    timeout: int = 900,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    verbose: bool = False,
    include_trajectory: bool = True,
    env_file: Path | None = None,
    planner_lm: Any | None = None,
) -> dict[str, Any]:
    """Async version of ``run_react_chat_once`` using ``achat_turn``."""
    agent = build_react_chat_agent(
        docs_path=docs_path,
        react_max_iters=react_max_iters,
        rlm_max_iterations=rlm_max_iterations,
        rlm_max_llm_calls=rlm_max_llm_calls,
        timeout=timeout,
        secret_name=secret_name,
        volume_name=volume_name,
        verbose=verbose,
        env_file=env_file,
        planner_lm=planner_lm,
    )
    try:
        with dspy.context(lm=planner_lm) if planner_lm else _nullcontext():
            with agent:
                result = await agent.achat_turn(message)
                if not include_trajectory:
                    result.pop("trajectory", None)
                return result
    except Exception:
        agent.shutdown()
        raise


class _nullcontext:
    """Minimal no-op context manager (avoid importing contextlib)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


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
