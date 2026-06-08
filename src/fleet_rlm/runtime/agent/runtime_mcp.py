"""MCP tool discovery and agent rebuild for AgentRuntime."""

from __future__ import annotations

from typing import Any


async def attach_mcp_tools(runtime: Any, configs: Any | None = None) -> list[str]:
    """Attach MCP tools to *runtime* and rebuild its cognition module."""
    from fleet_rlm.runtime.tools.mcp_tools import MCPToolProvider, load_mcp_server_configs

    resolved = configs if configs is not None else load_mcp_server_configs()
    if not resolved:
        return []

    provider = MCPToolProvider(resolved)
    mcp_tools = await provider.connect()
    if not mcp_tools:
        await provider.aclose()
        return []

    await aclose_mcp(runtime)
    runtime._mcp_provider = provider
    runtime._mcp_tools = list(mcp_tools)

    runtime.tools = list(runtime._base_tools) + list(runtime._mcp_tools)
    runtime.react_tools = runtime.tools
    runtime.agent = runtime._build_agent(runtime.tools)
    return [getattr(tool, "name", str(tool)) for tool in mcp_tools]


async def aclose_mcp(runtime: Any) -> None:
    """Close any live MCP sessions on *runtime*."""
    provider = runtime._mcp_provider
    if provider is None:
        return
    runtime._mcp_provider = None
    runtime._mcp_tools = []
    await provider.aclose()
    runtime.tools = list(runtime._base_tools)
    runtime.react_tools = runtime.tools
    runtime.agent = runtime._build_agent(runtime.tools)
