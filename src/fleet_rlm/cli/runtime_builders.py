"""Shared runtime config builders for CLI entrypoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fleet_rlm.infrastructure.config.env import AppConfig


DEFAULT_SERVER_VOLUME_NAME = "rlm-volume-dspy"


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
    from fleet_rlm.infrastructure.mcp.server import MCPRuntimeConfig

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
