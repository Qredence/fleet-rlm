"""Optional FastMCP server exposing ReAct + RLM tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Any, Literal, cast

from fleet_rlm.cli import runners
from fleet_rlm.runtime.config import get_delegate_lm_from_env
from fleet_rlm.runtime.factory import build_chat_agent

if TYPE_CHECKING:
    from fleet_rlm.integrations.config.env import AppConfig


@dataclass
class MCPRuntimeConfig:
    secret_name: str = "LITELLM"
    volume_name: str | None = None
    timeout: int = 900
    react_max_iters: int = 15
    deep_react_max_iters: int = 35
    enable_adaptive_iters: bool = True
    rlm_max_iterations: int = 30
    rlm_max_llm_calls: int = 50
    rlm_max_depth: int = 2
    delegate_max_calls_per_turn: int = 8
    delegate_result_truncation_chars: int = 8000
    interpreter_async_execute: bool = True
    agent_guardrail_mode: Literal["off", "warn", "strict"] = "off"
    agent_min_substantive_chars: int = 20
    agent_max_output_chars: int = 10000
    agent_delegate_model: str | None = None
    agent_delegate_max_tokens: int = 64000

    @classmethod
    def from_app_config(cls, config: AppConfig) -> MCPRuntimeConfig:
        """Build MCP runtime settings from the shared application config."""
        return cls(
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


def _chat_agent_kwargs(
    *,
    cfg: MCPRuntimeConfig,
    delegate_lm: Any | None,
    docs_path: str | None,
) -> dict[str, Any]:
    return {
        "docs_path": docs_path,
        "react_max_iters": cfg.react_max_iters,
        "deep_react_max_iters": cfg.deep_react_max_iters,
        "enable_adaptive_iters": cfg.enable_adaptive_iters,
        "rlm_max_iterations": cfg.rlm_max_iterations,
        "rlm_max_llm_calls": cfg.rlm_max_llm_calls,
        "max_depth": cfg.rlm_max_depth,
        "timeout": cfg.timeout,
        "secret_name": cfg.secret_name,
        "volume_name": cfg.volume_name,
        "interpreter_async_execute": cfg.interpreter_async_execute,
        "guardrail_mode": cfg.agent_guardrail_mode,
        "max_output_chars": cfg.agent_max_output_chars,
        "min_substantive_chars": cfg.agent_min_substantive_chars,
        "delegate_lm": delegate_lm,
        "delegate_max_calls_per_turn": cfg.delegate_max_calls_per_turn,
        "delegate_result_truncation_chars": cfg.delegate_result_truncation_chars,
    }


def _run_with_chat_agent(
    *,
    cfg: MCPRuntimeConfig,
    delegate_lm: Any | None,
    docs_path: str | None,
    operation: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    with build_chat_agent(
        **_chat_agent_kwargs(
            cfg=cfg,
            delegate_lm=delegate_lm,
            docs_path=docs_path,
        )
    ) as agent:
        return operation(agent)


def create_mcp_server(*, config: MCPRuntimeConfig | None = None):
    """Create a FastMCP server with chat-oriented tools."""
    fastmcp = cast(Any, import_module("fastmcp"))
    FastMCP = fastmcp.FastMCP

    cfg = config or MCPRuntimeConfig()
    server = FastMCP(name="fleet-rlm")
    delegate_lm = get_delegate_lm_from_env(
        model_name=cfg.agent_delegate_model,
        default_max_tokens=cfg.agent_delegate_max_tokens,
    )

    @server.tool
    def chat_turn(
        message: str, docs_path: str | None = None, trace: bool = False
    ) -> dict:
        return runners.run_react_chat_once(
            message=message,
            docs_path=docs_path,
            react_max_iters=cfg.react_max_iters,
            deep_react_max_iters=cfg.deep_react_max_iters,
            enable_adaptive_iters=cfg.enable_adaptive_iters,
            rlm_max_iterations=cfg.rlm_max_iterations,
            rlm_max_llm_calls=cfg.rlm_max_llm_calls,
            max_depth=cfg.rlm_max_depth,
            timeout=cfg.timeout,
            secret_name=cfg.secret_name,
            volume_name=cfg.volume_name,
            interpreter_async_execute=cfg.interpreter_async_execute,
            guardrail_mode=cfg.agent_guardrail_mode,
            max_output_chars=cfg.agent_max_output_chars,
            min_substantive_chars=cfg.agent_min_substantive_chars,
            delegate_lm=delegate_lm,
            delegate_max_calls_per_turn=cfg.delegate_max_calls_per_turn,
            delegate_result_truncation_chars=cfg.delegate_result_truncation_chars,
            include_trajectory=trace,
        )

    @server.tool
    def summarize_long_document(
        docs_path: str,
        query: str,
        include_trajectory: bool = True,
    ) -> dict:
        return runners.run_long_context(
            docs_path=docs_path,
            query=query,
            mode="summarize",
            max_iterations=cfg.rlm_max_iterations,
            max_llm_calls=cfg.rlm_max_llm_calls,
            timeout=cfg.timeout,
            secret_name=cfg.secret_name,
            volume_name=cfg.volume_name,
            include_trajectory=include_trajectory,
        )

    @server.tool
    def grounded_answer(
        docs_path: str,
        query: str,
        chunk_strategy: str = "headers",
        max_chunks: int = 24,
        include_trajectory: bool = True,
    ) -> dict:
        return _run_with_chat_agent(
            cfg=cfg,
            delegate_lm=delegate_lm,
            docs_path=docs_path,
            operation=lambda agent: agent.grounded_answer(
                query=query,
                chunk_strategy=chunk_strategy,
                max_chunks=max_chunks,
                include_trajectory=include_trajectory,
            ),
        )

    @server.tool
    def triage_incident_logs(
        docs_path: str,
        query: str,
        service_context: str = "",
        include_trajectory: bool = True,
    ) -> dict:
        return _run_with_chat_agent(
            cfg=cfg,
            delegate_lm=delegate_lm,
            docs_path=docs_path,
            operation=lambda agent: agent.triage_incident_logs(
                query=query,
                service_context=service_context,
                include_trajectory=include_trajectory,
            ),
        )

    @server.tool
    def memory_tree(
        docs_path: str | None = None,
        root_path: str = "/data/memory",
        max_depth: int = 4,
        include_hidden: bool = False,
    ) -> dict:
        return _run_with_chat_agent(
            cfg=cfg,
            delegate_lm=delegate_lm,
            docs_path=docs_path,
            operation=lambda agent: agent.memory_tree(
                root_path=root_path,
                max_depth=max_depth,
                include_hidden=include_hidden,
            ),
        )

    @server.tool
    def memory_structure_audit(
        docs_path: str | None = None,
        usage_goals: str = "",
    ) -> dict:
        return _run_with_chat_agent(
            cfg=cfg,
            delegate_lm=delegate_lm,
            docs_path=docs_path,
            operation=lambda agent: agent.memory_structure_audit(
                usage_goals=usage_goals
            ),
        )

    @server.tool
    def clarification_questions(
        request: str,
        docs_path: str | None = None,
        operation_risk: str = "medium",
    ) -> dict:
        return _run_with_chat_agent(
            cfg=cfg,
            delegate_lm=delegate_lm,
            docs_path=docs_path,
            operation=lambda agent: agent.clarification_questions(
                request=request,
                operation_risk=operation_risk,
            ),
        )

    return server
