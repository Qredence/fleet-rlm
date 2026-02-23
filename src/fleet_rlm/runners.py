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

import os
from pathlib import Path
from typing import Any, Literal

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
    react_max_iters: int = 5,
    rlm_max_iterations: int = 30,
    rlm_max_llm_calls: int = 50,
    max_depth: int = 2,
    timeout: int = 900,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    verbose: bool = False,
    history_max_turns: int | None = None,
    extra_tools: list | None = None,
    env_file: Path | None = None,
    planner_lm: Any | None = None,
    interpreter_async_execute: bool = True,
    guardrail_mode: Literal["off", "warn", "strict"] = "off",
    max_output_chars: int = 10000,
    min_substantive_chars: int = 20,
) -> RLMReActChatAgent:
    """Build an interactive DSPy ReAct chat agent for RLM workflows.

    Args:
        docs_path: Optional path to preload as the active document.
        react_max_iters: Maximum DSPy ReAct tool-iteration loops.
        rlm_max_iterations: Maximum iterations for internal ``dspy.RLM`` tools.
        rlm_max_llm_calls: Maximum LLM calls for internal RLM/interpreter usage.
        max_depth: Maximum recursion depth for sub-agent spawning.
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
        interpreter_async_execute: Whether to use non-blocking async execution
            for interpreter ``aexecute``.
        guardrail_mode: Guardrail behavior for assistant responses.
        max_output_chars: Maximum allowed assistant response length.
        min_substantive_chars: Soft warning threshold for brief responses.

    Returns:
        A configured ``RLMReActChatAgent`` instance.
    """
    if planner_lm is None:
        _require_planner_ready(env_file)

    agent = RLMReActChatAgent(
        react_max_iters=react_max_iters,
        rlm_max_iterations=rlm_max_iterations,
        rlm_max_llm_calls=rlm_max_llm_calls,
        max_depth=max_depth,
        timeout=timeout,
        secret_name=secret_name,
        volume_name=volume_name,
        verbose=verbose,
        history_max_turns=history_max_turns,
        extra_tools=extra_tools,
        interpreter_async_execute=interpreter_async_execute,
        guardrail_mode=guardrail_mode,
        max_output_chars=max_output_chars,
        min_substantive_chars=min_substantive_chars,
    )

    if docs_path is not None:
        agent.load_document(str(docs_path), alias="active")

    return agent


def run_react_chat_once(
    *,
    message: str,
    docs_path: Path | str | None = None,
    react_max_iters: int = 5,
    rlm_max_iterations: int = 30,
    rlm_max_llm_calls: int = 50,
    max_depth: int = 2,
    timeout: int = 900,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    verbose: bool = False,
    include_trajectory: bool = True,
    env_file: Path | None = None,
    interpreter_async_execute: bool = True,
    guardrail_mode: Literal["off", "warn", "strict"] = "off",
    max_output_chars: int = 10000,
    min_substantive_chars: int = 20,
) -> dict[str, Any]:
    """Run a single prompt through the interactive ReAct chat agent."""
    with build_react_chat_agent(
        docs_path=docs_path,
        react_max_iters=react_max_iters,
        rlm_max_iterations=rlm_max_iterations,
        rlm_max_llm_calls=rlm_max_llm_calls,
        max_depth=max_depth,
        timeout=timeout,
        secret_name=secret_name,
        volume_name=volume_name,
        verbose=verbose,
        env_file=env_file,
        interpreter_async_execute=interpreter_async_execute,
        guardrail_mode=guardrail_mode,
        max_output_chars=max_output_chars,
        min_substantive_chars=min_substantive_chars,
    ) as agent:
        result = agent.chat_turn(message)
        if not include_trajectory:
            result.pop("trajectory", None)
        return result


async def arun_react_chat_once(
    *,
    message: str,
    docs_path: Path | str | None = None,
    react_max_iters: int = 5,
    rlm_max_iterations: int = 30,
    rlm_max_llm_calls: int = 50,
    max_depth: int = 2,
    timeout: int = 900,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    verbose: bool = False,
    include_trajectory: bool = True,
    env_file: Path | None = None,
    planner_lm: Any | None = None,
    interpreter_async_execute: bool = True,
    guardrail_mode: Literal["off", "warn", "strict"] = "off",
    max_output_chars: int = 10000,
    min_substantive_chars: int = 20,
) -> dict[str, Any]:
    """Async version of ``run_react_chat_once`` using ``achat_turn``."""
    agent = build_react_chat_agent(
        docs_path=docs_path,
        react_max_iters=react_max_iters,
        rlm_max_iterations=rlm_max_iterations,
        rlm_max_llm_calls=rlm_max_llm_calls,
        max_depth=max_depth,
        timeout=timeout,
        secret_name=secret_name,
        volume_name=volume_name,
        verbose=verbose,
        env_file=env_file,
        planner_lm=planner_lm,
        interpreter_async_execute=interpreter_async_execute,
        guardrail_mode=guardrail_mode,
        max_output_chars=max_output_chars,
        min_substantive_chars=min_substantive_chars,
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


def _demo_tasks_enabled() -> bool:
    raw = os.getenv("FLEET_DEMO_TASKS_ENABLED", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _require_demo_tasks_enabled(function_name: str) -> None:
    if _demo_tasks_enabled():
        return
    raise RuntimeError(
        f"{function_name} is a demo runner and is disabled by default. "
        "Set FLEET_DEMO_TASKS_ENABLED=true to enable demo runners in local/dev use."
    )


def _load_runners_demos():
    # Imported lazily to avoid exposing/loading demo runners in default production paths.
    from . import runners_demos

    return runners_demos


def run_basic(*args: Any, **kwargs: Any) -> dict[str, Any]:
    _require_demo_tasks_enabled("run_basic")
    return _load_runners_demos().run_basic(*args, **kwargs)


def run_architecture(*args: Any, **kwargs: Any) -> dict[str, Any]:
    _require_demo_tasks_enabled("run_architecture")
    return _load_runners_demos().run_architecture(*args, **kwargs)


def run_api_endpoints(*args: Any, **kwargs: Any) -> dict[str, Any]:
    _require_demo_tasks_enabled("run_api_endpoints")
    return _load_runners_demos().run_api_endpoints(*args, **kwargs)


def run_error_patterns(*args: Any, **kwargs: Any) -> dict[str, Any]:
    _require_demo_tasks_enabled("run_error_patterns")
    return _load_runners_demos().run_error_patterns(*args, **kwargs)


def run_trajectory(*args: Any, **kwargs: Any) -> dict[str, Any]:
    _require_demo_tasks_enabled("run_trajectory")
    return _load_runners_demos().run_trajectory(*args, **kwargs)


def run_custom_tool(*args: Any, **kwargs: Any) -> dict[str, Any]:
    _require_demo_tasks_enabled("run_custom_tool")
    return _load_runners_demos().run_custom_tool(*args, **kwargs)


def check_secret_presence(*args: Any, **kwargs: Any) -> dict[str, Any]:
    # Diagnostic helper remains available in default mode.
    return _load_runners_demos().check_secret_presence(*args, **kwargs)


def check_secret_key(*args: Any, **kwargs: Any) -> dict[str, Any]:
    # Diagnostic helper remains available in default mode.
    return _load_runners_demos().check_secret_key(*args, **kwargs)


def run_long_context(*args: Any, **kwargs: Any) -> dict[str, Any]:
    # Kept available by default because this path is used by non-demo surfaces
    # (MCP + terminal helpers) despite being implemented in runners_demos.py today.
    return _load_runners_demos().run_long_context(*args, **kwargs)
