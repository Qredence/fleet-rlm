"""Canonical runtime construction helpers for FastAPI, CLI, and MCP."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import dspy

from fleet_rlm.runtime.agent.agent import FleetAgent  # noqa: F401
from fleet_rlm.runtime.agent.runtime import AgentRuntime
from fleet_rlm.runtime.config import configure_planner_from_env


def _require_planner_ready(env_file: Path | None = None) -> None:
    """Ensure the DSPy planner LM is configured."""
    ready = configure_planner_from_env(env_file=env_file)
    if not ready and dspy.settings.lm is None:
        raise RuntimeError(
            "Planner LM not configured. Set DSPY_LM_MODEL and DSPY_LLM_API_KEY (or DSPY_LM_API_KEY)."
        )


def build_chat_agent(
    *,
    docs_path: Path | str | None = None,
    react_max_iters: int = 15,
    deep_react_max_iters: int = 35,
    enable_adaptive_iters: bool = True,
    rlm_max_iterations: int = 30,
    rlm_max_llm_calls: int = 50,
    max_depth: int = 2,
    timeout: int = 900,
    secret_name: str = "LITELLM",
    volume_name: str | None = None,
    runtime: Any | None = None,
    verbose: bool = False,
    history_max_turns: int | None = None,
    extra_tools: list[Callable[..., Any]] | None = None,
    env_file: Path | None = None,
    planner_lm: Any | None = None,
    interpreter: Any | None = None,
    interpreter_async_execute: bool = True,
    delete_session_on_shutdown: bool = False,
    sandbox_spec: Any | None = None,
    sub_lm: Any | None = None,
    guardrail_mode: Literal["off", "warn", "strict"] = "warn",
    max_output_chars: int = 10000,
    min_substantive_chars: int = 20,
    delegate_lm: Any | None = None,
    delegate_max_calls_per_turn: int = 8,
    delegate_result_truncation_chars: int = 8000,
) -> Any:
    """Build the canonical DSPy chat agent using FleetAgent and AgentRuntime."""
    _ = (
        deep_react_max_iters,
        enable_adaptive_iters,
        rlm_max_iterations,
        rlm_max_llm_calls,
        max_depth,
        timeout,
        secret_name,
        volume_name,
        runtime,
        verbose,
        interpreter_async_execute,
        delete_session_on_shutdown,
        sandbox_spec,
        sub_lm,
        guardrail_mode,
        max_output_chars,
        min_substantive_chars,
        delegate_lm,
        delegate_max_calls_per_turn,
        delegate_result_truncation_chars,
    )

    if planner_lm is None:
        _require_planner_ready(env_file)

    agent = AgentRuntime(
        interpreter=interpreter,
        max_iters=react_max_iters,
        history_max_turns=history_max_turns,
        extra_tools=extra_tools,
    )
    if docs_path is not None:
        agent.load_document(str(docs_path), alias="active")

    return agent


__all__ = [
    "_require_planner_ready",
    "build_chat_agent",
]
