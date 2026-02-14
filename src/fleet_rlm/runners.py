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

from pathlib import Path
from typing import Any

import dspy

from .core.config import configure_planner_from_env
from .core.interpreter import ModalInterpreter
from .react.agent import RLMReActChatAgent


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


from .runners_demos import (  # noqa: E402, F401
    check_secret_key,
    check_secret_presence,
    run_api_endpoints,
    run_architecture,
    run_basic,
    run_custom_tool,
    run_error_patterns,
    run_long_context,
    run_trajectory,
)
