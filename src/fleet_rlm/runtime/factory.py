"""Canonical runtime construction helpers for FastAPI and CLI."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fleet_rlm.runtime.config import configure_planner_from_env


def _require_planner_ready(env_file: Path | None = None) -> None:
    """Ensure the DSPy planner LM is configured."""
    import dspy

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
    rlm_child_isolation_mode: Literal["auto", "context"] = "auto",
    rlm_child_fork_fallback: Literal["clean", "fail"] = "clean",
    repository: Any | None = None,
) -> Any:
    """Build the canonical DSPy chat agent using FleetAgent and AgentRuntime."""
    from fleet_rlm.runtime.agent.runtime import AgentRuntime

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
        guardrail_mode,
        max_output_chars,
        min_substantive_chars,
        delegate_max_calls_per_turn,
        delegate_result_truncation_chars,
        rlm_child_isolation_mode,
        rlm_child_fork_fallback,
    )

    if planner_lm is None:
        _require_planner_ready(env_file)

    if interpreter is not None:
        effective_sub_lm = sub_lm if sub_lm is not None else delegate_lm
        if effective_sub_lm is not None:
            setattr(interpreter, "sub_lm", effective_sub_lm)
        setattr(interpreter, "rlm_max_iterations", max(1, int(rlm_max_iterations)))
        setattr(interpreter, "max_llm_calls", max(1, int(rlm_max_llm_calls)))
        setattr(interpreter, "_sub_rlm_max_depth", max(0, int(max_depth)))
        setattr(interpreter, "child_isolation_mode", rlm_child_isolation_mode)
        setattr(interpreter, "child_fork_fallback", rlm_child_fork_fallback)
        setattr(
            interpreter,
            "delegate_max_calls_per_turn",
            max(1, int(delegate_max_calls_per_turn)),
        )
        setattr(
            interpreter,
            "delegate_result_truncation_chars",
            max(0, int(delegate_result_truncation_chars)),
        )

    agent = AgentRuntime(
        interpreter=interpreter,
        max_iters=react_max_iters,
        history_max_turns=history_max_turns,
        extra_tools=extra_tools,
        repository=repository,
    )
    if docs_path is not None:
        agent.load_document(str(docs_path), alias="active")

    return agent


__all__ = [
    "_require_planner_ready",
    "build_chat_agent",
]
