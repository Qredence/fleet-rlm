"""DSPy ReAct tool registry for the RLM chat agent."""

from __future__ import annotations

from ._marker import tool_fn
from .mcp_tools import (
    MCP_SERVERS_ENV_VAR,
    MCPServerConfig,
    MCPToolProvider,
    discover_mcp_tools,
    load_mcp_server_configs,
)
from .registry import (
    TOOL_MODULE_NAMES,
    _collect_tools_from_modules,
    discover_tools,
    list_react_tool_names,
)

__all__ = [
    "MCP_SERVERS_ENV_VAR",
    "MCPServerConfig",
    "MCPToolProvider",
    "TOOL_MODULE_NAMES",
    "_collect_tools_from_modules",
    "discover_mcp_tools",
    "discover_tools",
    "list_react_tool_names",
    "load_mcp_server_configs",
    "tool_fn",
]
