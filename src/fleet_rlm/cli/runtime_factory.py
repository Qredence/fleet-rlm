"""Shared CLI/runtime construction helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import dspy

from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent
from fleet_rlm.runtime.config import configure_planner_from_env

if TYPE_CHECKING:
    from fleet_rlm.integrations.config.env import AppConfig


DEFAULT_SERVER_VOLUME_NAME = "rlm-volume-dspy"


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
    guardrail_mode: Literal["off", "warn", "strict"] = "off"
    max_output_chars: int = 10000
    min_substantive_chars: int = 20
    delegate_lm: Any | None = None
    delegate_max_calls_per_turn: int = 8
    delegate_result_truncation_chars: int = 8000


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


def _build_react_agent_from_options(
    *,
    options: _ReActAgentOptions,
    docs_path: Path | str | None = None,
    env_file: Path | None = None,
    planner_lm: Any | None = None,
) -> RLMReActChatAgent:
    """Build a chat agent from consolidated options with stable behavior."""
    if planner_lm is None:
        _require_planner_ready(env_file)

    agent = RLMReActChatAgent(
        react_max_iters=options.react_max_iters,
        deep_react_max_iters=options.deep_react_max_iters,
        enable_adaptive_iters=options.enable_adaptive_iters,
        rlm_max_iterations=options.rlm_max_iterations,
        rlm_max_llm_calls=options.rlm_max_llm_calls,
        max_depth=options.max_depth,
        timeout=options.timeout,
        secret_name=options.secret_name,
        volume_name=options.volume_name,
        verbose=options.verbose,
        history_max_turns=options.history_max_turns,
        extra_tools=options.extra_tools,
        interpreter_async_execute=options.interpreter_async_execute,
        guardrail_mode=options.guardrail_mode,
        max_output_chars=options.max_output_chars,
        min_substantive_chars=options.min_substantive_chars,
        delegate_lm=options.delegate_lm,
        delegate_max_calls_per_turn=options.delegate_max_calls_per_turn,
        delegate_result_truncation_chars=options.delegate_result_truncation_chars,
    )

    if docs_path is not None:
        agent.load_document(str(docs_path), alias="active")

    return agent


def _build_modal_chat_agent_from_options(
    *,
    options: _ReActAgentOptions,
    planner_lm: Any | None = None,
) -> RLMReActChatAgent:
    """Build the default shared chat agent backed by Modal."""
    return _build_react_agent_from_options(
        options=options,
        planner_lm=planner_lm,
    )


def _build_daytona_workbench_chat_agent_from_options(
    *,
    options: _ReActAgentOptions,
    planner_lm: Any | None = None,
) -> Any:
    """Build the Daytona chat runtime using shared ReAct-compatible options."""
    from fleet_rlm.integrations.providers.daytona.agent import (
        DaytonaWorkbenchChatAgent,
    )

    return DaytonaWorkbenchChatAgent(
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


def _build_runtime_mode_options(
    *,
    runtime_mode: str,
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
    interpreter_async_execute: bool = True,
    guardrail_mode: Literal["off", "warn", "strict"] = "off",
    max_output_chars: int = 10000,
    min_substantive_chars: int = 20,
    delegate_lm: Any | None = None,
    delegate_max_calls_per_turn: int = 8,
    delegate_result_truncation_chars: int = 8000,
) -> _ReActAgentOptions:
    """Build normalized shared chat options for the requested runtime mode."""
    is_daytona = runtime_mode == "daytona_pilot"
    return _ReActAgentOptions(
        react_max_iters=react_max_iters,
        deep_react_max_iters=deep_react_max_iters,
        enable_adaptive_iters=enable_adaptive_iters,
        rlm_max_iterations=rlm_max_iterations,
        rlm_max_llm_calls=rlm_max_llm_calls,
        max_depth=max_depth,
        timeout=timeout,
        secret_name=secret_name if not is_daytona else "LITELLM",
        volume_name=volume_name if not is_daytona else None,
        verbose=verbose,
        history_max_turns=history_max_turns,
        interpreter_async_execute=interpreter_async_execute,
        guardrail_mode=guardrail_mode,
        max_output_chars=max_output_chars,
        min_substantive_chars=min_substantive_chars,
        delegate_lm=delegate_lm,
        delegate_max_calls_per_turn=delegate_max_calls_per_turn,
        delegate_result_truncation_chars=delegate_result_truncation_chars,
    )


def build_react_chat_agent(
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
    guardrail_mode: Literal["off", "warn", "strict"] = "off",
    max_output_chars: int = 10000,
    min_substantive_chars: int = 20,
    delegate_lm: Any | None = None,
    delegate_max_calls_per_turn: int = 8,
    delegate_result_truncation_chars: int = 8000,
) -> RLMReActChatAgent:
    """Build an interactive DSPy ReAct chat agent for RLM workflows.

    Args:
        docs_path: Optional path to preload as the active document.
        react_max_iters: Maximum DSPy ReAct tool-iteration loops.
        deep_react_max_iters: Escalated iteration budget for deep analysis turns.
        enable_adaptive_iters: Toggle adaptive turn budgeting.
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
        delegate_lm: Optional lower-cost LM used for delegate sub-agent turns.
        delegate_max_calls_per_turn: Per-turn delegate spawn budget.
        delegate_result_truncation_chars: Delegate output truncation limit.

    Returns:
        A configured ``RLMReActChatAgent`` instance.
    """
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


def build_chat_agent_for_runtime_mode(
    *,
    runtime_mode: str = "modal_chat",
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
    planner_lm: Any | None = None,
    interpreter_async_execute: bool = True,
    guardrail_mode: Literal["off", "warn", "strict"] = "off",
    max_output_chars: int = 10000,
    min_substantive_chars: int = 20,
    delegate_lm: Any | None = None,
    delegate_max_calls_per_turn: int = 8,
    delegate_result_truncation_chars: int = 8000,
) -> Any:
    """Build a chat agent using provider capabilities instead of mode branches.

    Modal consumes ``secret_name``/``volume_name`` during construction. Daytona
    reuses the shared ReAct settings but defers workspace/session wiring until
    request-time, so those Modal-only settings are stripped automatically.
    """
    options = _build_runtime_mode_options(
        runtime_mode=runtime_mode,
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
        interpreter_async_execute=interpreter_async_execute,
        guardrail_mode=guardrail_mode,
        max_output_chars=max_output_chars,
        min_substantive_chars=min_substantive_chars,
        delegate_lm=delegate_lm,
        delegate_max_calls_per_turn=delegate_max_calls_per_turn,
        delegate_result_truncation_chars=delegate_result_truncation_chars,
    )
    if runtime_mode == "daytona_pilot":
        return _build_daytona_workbench_chat_agent_from_options(
            options=options,
            planner_lm=planner_lm,
        )
    return _build_modal_chat_agent_from_options(
        options=options,
        planner_lm=planner_lm,
    )


def build_daytona_workbench_chat_agent(
    *,
    timeout: int = 900,
    max_depth: int = 2,
    history_max_turns: int | None = None,
    planner_lm: Any | None = None,
    delegate_lm: Any | None = None,
    interpreter_async_execute: bool = True,
) -> Any:
    """Compatibility wrapper for the Daytona shared-runtime chat builder."""
    return build_chat_agent_for_runtime_mode(
        runtime_mode="daytona_pilot",
        timeout=timeout,
        max_depth=max_depth,
        history_max_turns=history_max_turns,
        planner_lm=planner_lm,
        delegate_lm=delegate_lm,
        interpreter_async_execute=interpreter_async_execute,
    )


def resolve_server_volume_name(config: AppConfig) -> str | None:
    """Resolve the server-side volume name from config, falling back to default."""
    volume_name = config.interpreter.volume_name
    return volume_name if volume_name is not None else DEFAULT_SERVER_VOLUME_NAME


def build_server_runtime_config(config: AppConfig):
    """Build the FastAPI server runtime config from the shared app config."""
    from fleet_rlm.api.config import ServerRuntimeConfig

    return ServerRuntimeConfig(
        secret_name=config.interpreter.secrets[0]
        if config.interpreter.secrets
        else "LITELLM",
        volume_name=resolve_server_volume_name(config),
        timeout=config.interpreter.timeout,
        react_max_iters=config.rlm_settings.max_iters,
        deep_react_max_iters=config.rlm_settings.deep_max_iters,
        enable_adaptive_iters=config.rlm_settings.enable_adaptive_iters,
        rlm_max_iterations=config.agent.rlm_max_iterations,
        rlm_max_llm_calls=config.rlm_settings.max_llm_calls,
        rlm_max_depth=config.rlm_settings.max_depth,
        delegate_max_calls_per_turn=config.rlm_settings.delegate_max_calls_per_turn,
        delegate_result_truncation_chars=config.rlm_settings.delegate_result_truncation_chars,
        interpreter_async_execute=config.interpreter.async_execute,
        agent_guardrail_mode=config.agent.guardrail_mode,
        agent_min_substantive_chars=config.agent.min_substantive_chars,
        agent_max_output_chars=config.rlm_settings.max_output_chars,
        agent_model=config.agent.model,
        agent_delegate_model=config.agent.delegate_model,
        agent_delegate_max_tokens=config.agent.delegate_max_tokens,
        db_validate_on_startup=True,
    )


def build_mcp_runtime_config(config: AppConfig):
    """Build the MCP server runtime config from the shared app config."""
    from fleet_rlm.integrations.mcp.server import MCPRuntimeConfig

    return MCPRuntimeConfig(
        secret_name=config.interpreter.secrets[0]
        if config.interpreter.secrets
        else "LITELLM",
        volume_name=config.interpreter.volume_name,
        timeout=config.interpreter.timeout,
        react_max_iters=config.rlm_settings.max_iters,
        rlm_max_iterations=config.agent.rlm_max_iterations,
        rlm_max_llm_calls=config.rlm_settings.max_llm_calls,
        rlm_max_depth=config.rlm_settings.max_depth,
        deep_react_max_iters=config.rlm_settings.deep_max_iters,
        enable_adaptive_iters=config.rlm_settings.enable_adaptive_iters,
        delegate_max_calls_per_turn=config.rlm_settings.delegate_max_calls_per_turn,
        delegate_result_truncation_chars=config.rlm_settings.delegate_result_truncation_chars,
        interpreter_async_execute=config.interpreter.async_execute,
        agent_guardrail_mode=config.agent.guardrail_mode,
        agent_min_substantive_chars=config.agent.min_substantive_chars,
        agent_max_output_chars=config.rlm_settings.max_output_chars,
        agent_delegate_model=config.agent.delegate_model,
        agent_delegate_max_tokens=config.agent.delegate_max_tokens,
    )
