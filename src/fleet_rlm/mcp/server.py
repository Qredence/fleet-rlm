"""Optional FastMCP server exposing ReAct + RLM tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fleet_rlm import runners


@dataclass
class MCPRuntimeConfig:
    secret_name: str = "LITELLM"
    volume_name: str | None = None
    timeout: int = 900
    react_max_iters: int = 10
    rlm_max_iterations: int = 30
    rlm_max_llm_calls: int = 50
    rlm_max_depth: int = 2
    interpreter_async_execute: bool = True
    agent_guardrail_mode: Literal["off", "warn", "strict"] = "off"
    agent_min_substantive_chars: int = 20
    agent_max_output_chars: int = 10000


def create_mcp_server(*, config: MCPRuntimeConfig | None = None):
    """Create a FastMCP server with chat-oriented tools."""
    from fastmcp import FastMCP  # type: ignore

    cfg = config or MCPRuntimeConfig()
    server = FastMCP(name="fleet-rlm")

    @server.tool
    def chat_turn(
        message: str, docs_path: str | None = None, trace: bool = False
    ) -> dict:
        return runners.run_react_chat_once(
            message=message,
            docs_path=docs_path,
            react_max_iters=cfg.react_max_iters,
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
            include_trajectory=trace,
        )

    @server.tool
    def analyze_long_document(
        docs_path: str,
        query: str,
        include_trajectory: bool = True,
    ) -> dict:
        return runners.run_long_context(
            docs_path=docs_path,
            query=query,
            mode="analyze",
            max_iterations=cfg.rlm_max_iterations,
            max_llm_calls=cfg.rlm_max_llm_calls,
            timeout=cfg.timeout,
            secret_name=cfg.secret_name,
            volume_name=cfg.volume_name,
            include_trajectory=include_trajectory,
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
        with runners.build_react_chat_agent(
            docs_path=docs_path,
            react_max_iters=cfg.react_max_iters,
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
        ) as agent:
            return agent.grounded_answer(
                query=query,
                chunk_strategy=chunk_strategy,
                max_chunks=max_chunks,
                include_trajectory=include_trajectory,
            )

    @server.tool
    def triage_incident_logs(
        docs_path: str,
        query: str,
        service_context: str = "",
        include_trajectory: bool = True,
    ) -> dict:
        with runners.build_react_chat_agent(
            docs_path=docs_path,
            react_max_iters=cfg.react_max_iters,
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
        ) as agent:
            return agent.triage_incident_logs(
                query=query,
                service_context=service_context,
                include_trajectory=include_trajectory,
            )

    @server.tool
    def memory_tree(
        docs_path: str | None = None,
        root_path: str = "/data/memory",
        max_depth: int = 4,
        include_hidden: bool = False,
    ) -> dict:
        with runners.build_react_chat_agent(
            docs_path=docs_path,
            react_max_iters=cfg.react_max_iters,
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
        ) as agent:
            return agent.memory_tree(
                root_path=root_path,
                max_depth=max_depth,
                include_hidden=include_hidden,
            )

    @server.tool
    def memory_structure_audit(
        docs_path: str | None = None,
        usage_goals: str = "",
    ) -> dict:
        with runners.build_react_chat_agent(
            docs_path=docs_path,
            react_max_iters=cfg.react_max_iters,
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
        ) as agent:
            return agent.memory_structure_audit(usage_goals=usage_goals)

    @server.tool
    def clarification_questions(
        request: str,
        docs_path: str | None = None,
        operation_risk: str = "medium",
    ) -> dict:
        with runners.build_react_chat_agent(
            docs_path=docs_path,
            react_max_iters=cfg.react_max_iters,
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
        ) as agent:
            return agent.clarification_questions(
                request=request,
                operation_risk=operation_risk,
            )

    return server
