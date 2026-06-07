"""DSPy-native MCP (Model Context Protocol) tool integration for the runtime.

This module connects to configured MCP servers over stdio, lists their tools,
and wraps each one as a :class:`dspy.Tool` via the canonical
:meth:`dspy.Tool.from_mcp_tool` bridge (which delegates to
``dspy.utils.mcp.convert_mcp_tool``). The wrapped tools are async-only, matching
the MCP protocol, so they plug directly into the ReAct ``acall`` path.

Design notes:

- No import-time side effects. The ``mcp`` package and DSPy MCP helpers are
  imported lazily inside the connection coroutine.
- Server definitions come from the ``FLEET_RLM_MCP_SERVERS`` environment
  variable (env-first; richer settings can layer on later).
- :class:`MCPToolProvider` owns the live session lifecycle through an
  :class:`contextlib.AsyncExitStack`; callers must ``await provider.aclose()``
  (or use it as an async context manager) to release the sessions.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MCP_SERVERS_ENV_VAR = "FLEET_RLM_MCP_SERVERS"


@dataclass(slots=True)
class MCPServerConfig:
    """Connection definition for a single stdio MCP server."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> MCPServerConfig:
        """Build a config from a plain mapping, validating required fields."""
        name = str(raw.get("name", "")).strip()
        command = str(raw.get("command", "")).strip()
        if not name or not command:
            raise ValueError("Each MCP server config requires non-empty 'name' and 'command' fields.")
        args = [str(item) for item in (raw.get("args") or [])]
        env_raw = raw.get("env") or {}
        if not isinstance(env_raw, dict):
            raise ValueError(f"MCP server {name!r} 'env' must be a mapping if provided.")
        env = {str(key): str(value) for key, value in env_raw.items()}
        return cls(name=name, command=command, args=args, env=env)


def load_mcp_server_configs(raw_json: str | None = None) -> list[MCPServerConfig]:
    """Parse MCP server configs from JSON (defaults to the env var).

    The value must be a JSON array of objects, each with ``name`` and
    ``command`` (and optional ``args`` / ``env``). Returns an empty list when
    unset or empty so MCP stays opt-in.
    """
    payload = raw_json if raw_json is not None else os.environ.get(MCP_SERVERS_ENV_VAR, "")
    payload = payload.strip()
    if not payload:
        return []

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{MCP_SERVERS_ENV_VAR} must be valid JSON: {exc}") from exc

    if not isinstance(parsed, list):
        raise ValueError(f"{MCP_SERVERS_ENV_VAR} must be a JSON array of server objects.")

    return [MCPServerConfig.from_mapping(item) for item in parsed]


class MCPToolProvider:
    """Manages live MCP sessions and exposes their tools as ``dspy.Tool`` objects.

    Usage::

        provider = MCPToolProvider(load_mcp_server_configs())
        tools = await provider.connect()
        try:
            ...  # use tools in the ReAct loop
        finally:
            await provider.aclose()

    or as an async context manager::

        async with MCPToolProvider(configs) as provider:
            tools = provider.tools
    """

    def __init__(self, configs: list[MCPServerConfig]) -> None:
        self._configs = list(configs)
        self._exit_stack: AsyncExitStack | None = None
        self._tools: list[Any] = []

    @property
    def tools(self) -> list[Any]:
        """Wrapped MCP tools discovered during :meth:`connect`."""
        return list(self._tools)

    async def connect(self) -> list[Any]:
        """Open every configured server, list its tools, and wrap them.

        A failure connecting to one server is logged and skipped so a single
        bad server does not disable all MCP tooling. Returns the full list of
        wrapped tools (also available via :pyattr:`tools`).
        """
        if self._exit_stack is not None:
            return self.tools
        if not self._configs:
            return []

        import dspy
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        stack = AsyncExitStack()
        collected: list[Any] = []
        seen_names: set[str] = set()

        for config in self._configs:
            try:
                params = StdioServerParameters(
                    command=config.command,
                    args=config.args,
                    env={**os.environ, **config.env} if config.env else None,
                )
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listed = await session.list_tools()
            except Exception as exc:
                logger.warning("MCP server %r failed to connect, skipping: %s", config.name, exc)
                continue

            for mcp_tool in listed.tools:
                if mcp_tool.name in seen_names:
                    logger.warning(
                        "Duplicate MCP tool name %r from server %r ignored.",
                        mcp_tool.name,
                        config.name,
                    )
                    continue
                seen_names.add(mcp_tool.name)
                collected.append(dspy.Tool.from_mcp_tool(session, mcp_tool))

            logger.info("Connected MCP server %r with %d tool(s).", config.name, len(listed.tools))

        self._exit_stack = stack
        self._tools = collected
        return self.tools

    async def aclose(self) -> None:
        """Close all open MCP sessions and reset provider state."""
        if self._exit_stack is None:
            return
        try:
            await self._exit_stack.aclose()
        finally:
            self._exit_stack = None
            self._tools = []

    async def __aenter__(self) -> MCPToolProvider:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()


async def discover_mcp_tools(
    configs: list[MCPServerConfig] | None = None,
) -> tuple[MCPToolProvider, list[Any]]:
    """Connect to MCP servers and return the live provider plus its tools.

    The caller owns the returned provider and must ``await provider.aclose()``
    when the tools are no longer needed. When no configs are supplied they are
    loaded from the environment.
    """
    provider = MCPToolProvider(configs if configs is not None else load_mcp_server_configs())
    tools = await provider.connect()
    return provider, tools


__all__ = [
    "MCP_SERVERS_ENV_VAR",
    "MCPServerConfig",
    "MCPToolProvider",
    "discover_mcp_tools",
    "load_mcp_server_configs",
]
