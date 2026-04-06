"""Canonical runtime construction helpers for FastAPI, CLI, and MCP."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import dspy

from fleet_rlm.integrations.daytona.agent import DaytonaWorkbenchChatAgent
from fleet_rlm.runtime.config import configure_planner_from_env


@dataclass(slots=True)
class _ReActAgentOptions:
    """Internal configuration bundle used to build chat agents consistently."""

    react_max_iters: int = 15
    deep_react_max_iters: int = 35
    enable_adaptive_iters: bool = True
    rlm_max_iterations: int = 30
    rlm_max_llm_calls: int = 50
    max_depth: int = 2
    timeout: int = 900
    secret_name: str = "LITELLM"
    volume_name: str | None = None
    verbose: bool = False
    history_max_turns: int | None = None
    extra_tools: list | None = None
    interpreter_async_execute: bool = True
    guardrail_mode: Literal["off", "warn", "strict"] = "warn"
    max_output_chars: int = 10000
    min_substantive_chars: int = 20
    delegate_lm: Any | None = None
    delegate_max_calls_per_turn: int = 8
    delegate_result_truncation_chars: int = 8000


def _require_planner_ready(env_file: Path | None = None) -> None:
    """Ensure the DSPy planner LM is configured."""
    ready = configure_planner_from_env(env_file=env_file)
    if not ready and dspy.settings.lm is None:
        raise RuntimeError(
            "Planner LM not configured. Set DSPY_LM_MODEL and DSPY_LLM_API_KEY (or DSPY_LM_API_KEY)."
        )


def _build_react_agent_from_options(
    *,
    options: _ReActAgentOptions,
    docs_path: Path | str | None = None,
    env_file: Path | None = None,
    planner_lm: Any | None = None,
) -> DaytonaWorkbenchChatAgent:
    """Build the canonical Daytona-backed chat agent."""
    if planner_lm is None:
        _require_planner_ready(env_file)

    agent = DaytonaWorkbenchChatAgent(
        react_max_iters=options.react_max_iters,
        deep_react_max_iters=options.deep_react_max_iters,
        enable_adaptive_iters=options.enable_adaptive_iters,
        rlm_max_iterations=options.rlm_max_iterations,
        rlm_max_llm_calls=options.rlm_max_llm_calls,
        max_depth=options.max_depth,
        timeout=options.timeout,
        verbose=options.verbose,
        history_max_turns=options.history_max_turns,
        planner_lm=planner_lm,
        delegate_lm=options.delegate_lm,
        delete_session_on_shutdown=False,
        guardrail_mode=options.guardrail_mode,
        max_output_chars=options.max_output_chars,
        min_substantive_chars=options.min_substantive_chars,
        delegate_max_calls_per_turn=options.delegate_max_calls_per_turn,
        delegate_result_truncation_chars=options.delegate_result_truncation_chars,
        interpreter_async_execute=options.interpreter_async_execute,
    )

    if docs_path is not None:
        agent.load_document(str(docs_path), alias="active")

    return agent


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
    verbose: bool = False,
    history_max_turns: int | None = None,
    extra_tools: list | None = None,
    env_file: Path | None = None,
    planner_lm: Any | None = None,
    interpreter_async_execute: bool = True,
    guardrail_mode: Literal["off", "warn", "strict"] = "warn",
    max_output_chars: int = 10000,
    min_substantive_chars: int = 20,
    delegate_lm: Any | None = None,
    delegate_max_calls_per_turn: int = 8,
    delegate_result_truncation_chars: int = 8000,
) -> DaytonaWorkbenchChatAgent:
    """Build the canonical Daytona-backed DSPy chat agent."""
    options = _ReActAgentOptions(
        react_max_iters=react_max_iters,
        deep_react_max_iters=deep_react_max_iters,
        enable_adaptive_iters=enable_adaptive_iters,
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
        delegate_lm=delegate_lm,
        delegate_max_calls_per_turn=delegate_max_calls_per_turn,
        delegate_result_truncation_chars=delegate_result_truncation_chars,
    )
    return _build_react_agent_from_options(
        options=options,
        docs_path=docs_path,
        env_file=env_file,
        planner_lm=planner_lm,
    )


def build_react_chat_agent(**kwargs: Any) -> DaytonaWorkbenchChatAgent:
    """Compatibility alias for the canonical Daytona-backed agent builder."""
    return build_chat_agent(**kwargs)


def build_daytona_workbench_chat_agent(**kwargs: Any) -> DaytonaWorkbenchChatAgent:
    """Explicit Daytona builder alias for callers that prefer provider naming."""
    return build_chat_agent(**kwargs)


def build_chat_agent_for_runtime_mode(
    *,
    runtime_mode: str = "daytona_pilot",
    **kwargs: Any,
) -> DaytonaWorkbenchChatAgent:
    """Build the canonical chat agent, rejecting legacy Modal runtime modes."""
    normalized = str(runtime_mode or "daytona").strip().lower()
    if normalized not in {"daytona", "daytona_pilot"}:
        raise ValueError(
            "Only the Daytona runtime is supported. Remove runtime_mode selection and use the canonical execution runtime."
        )
    return build_chat_agent(**kwargs)


__all__ = [
    "_ReActAgentOptions",
    "_build_react_agent_from_options",
    "_require_planner_ready",
    "build_chat_agent",
    "build_chat_agent_for_runtime_mode",
    "build_daytona_workbench_chat_agent",
    "build_react_chat_agent",
]
