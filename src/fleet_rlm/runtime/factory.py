"""Canonical runtime construction helpers for FastAPI and CLI."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fleet_rlm.integrations.config._env_utils import env_bool as _env_bool
from fleet_rlm.runtime.config import configure_planner_from_env

ESCALATING_RUNTIME_ENV_VAR = "FLEET_RLM_USE_ESCALATING_RUNTIME"


def _require_planner_ready(env_file: Path | None = None) -> None:
    """Ensure the DSPy planner LM is configured."""
    import dspy

    ready = configure_planner_from_env(env_file=env_file)
    if not ready and dspy.settings.lm is None:
        raise RuntimeError("Planner LM not configured. Set DSPY_LM_MODEL and DSPY_LLM_API_KEY (or DSPY_LM_API_KEY).")


def _escalating_runtime_enabled(use_escalation: bool | None) -> bool:
    """Resolve the Phase 2 runtime toggle, defaulting to the escalating module."""
    if use_escalation is not None:
        return use_escalation
    return _env_bool(os.getenv(ESCALATING_RUNTIME_ENV_VAR), default=True)


def build_chat_agent(
    *,
    docs_path: Path | str | None = None,
    react_max_iters: int = 15,
    rlm_max_iterations: int | None = None,
    rlm_max_llm_calls: int | None = None,
    rlm_max_output_chars: int | None = None,
    rlm_action_max_tokens: int | None = None,
    history_max_turns: int | None = None,
    extra_tools: list[Callable[..., Any]] | None = None,
    env_file: Path | None = None,
    planner_lm: Any | None = None,
    interpreter: Any | None = None,
    sub_lm: Any | None = None,
    delegate_lm: Any | None = None,
    repository: Any | None = None,
    use_escalation: bool | None = None,
) -> Any:
    """Build the canonical DSPy chat agent with Phase 2 escalation enabled by default."""
    from fleet_rlm.runtime.agent.runtime import AgentRuntime

    if planner_lm is None:
        _require_planner_ready(env_file)

    if interpreter is not None:
        effective_sub_lm = sub_lm if sub_lm is not None else delegate_lm
        if effective_sub_lm is not None:
            interpreter.sub_lm = effective_sub_lm

    agent = AgentRuntime(
        interpreter=interpreter,
        max_iters=react_max_iters,
        rlm_max_iterations=rlm_max_iterations,
        rlm_max_llm_calls=rlm_max_llm_calls,
        rlm_max_output_chars=rlm_max_output_chars,
        rlm_action_max_tokens=rlm_action_max_tokens,
        history_max_turns=history_max_turns,
        extra_tools=extra_tools,
        repository=repository,
        use_escalation=_escalating_runtime_enabled(use_escalation),
    )
    if docs_path is not None:
        agent.load_document(str(docs_path), alias="active")

    return agent


__all__ = [
    "ESCALATING_RUNTIME_ENV_VAR",
    "_escalating_runtime_enabled",
    "_require_planner_ready",
    "build_chat_agent",
]
