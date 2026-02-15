"""Optional FastMCP server exposing ReAct + RLM tools."""

from __future__ import annotations

from dataclasses import dataclass

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


def create_mcp_server(*, config: MCPRuntimeConfig | None = None):
    """Create a FastMCP server with chat-oriented tools."""
    from fastmcp import FastMCP

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

    return server
