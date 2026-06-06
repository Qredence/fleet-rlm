"""Tests for the DSPy-native MCP tool provider.

Covers config parsing and an end-to-end stdio round-trip against a real
FastMCP server fixture, asserting that discovered tools are async ``dspy.Tool``
objects wired through ``dspy.Tool.from_mcp_tool``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import dspy
import pytest

from fleet_rlm.runtime.tools.mcp_tools import (
    MCP_SERVERS_ENV_VAR,
    MCPServerConfig,
    MCPToolProvider,
    load_mcp_server_configs,
)

_ECHO_SERVER = Path(__file__).resolve().parents[2] / "fixtures" / "mcp_echo_server.py"


def _tool(name: str) -> dspy.Tool:
    return dspy.Tool(
        func=lambda: name,
        name=name,
        desc=f"{name} tool",
        args={},
    )


def test_load_mcp_server_configs_empty_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MCP_SERVERS_ENV_VAR, raising=False)
    assert load_mcp_server_configs() == []
    assert load_mcp_server_configs("") == []
    assert load_mcp_server_configs("   ") == []


def test_load_mcp_server_configs_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        MCP_SERVERS_ENV_VAR,
        '[{"name": "demo", "command": "python", "args": ["-m", "srv"], "env": {"K": "V"}}]',
    )
    configs = load_mcp_server_configs()
    assert len(configs) == 1
    assert configs[0] == MCPServerConfig(name="demo", command="python", args=["-m", "srv"], env={"K": "V"})


def test_load_mcp_server_configs_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="must be valid JSON"):
        load_mcp_server_configs("{not json")


def test_load_mcp_server_configs_rejects_non_array() -> None:
    with pytest.raises(ValueError, match="must be a JSON array"):
        load_mcp_server_configs('{"name": "x"}')


def test_mcp_server_config_requires_name_and_command() -> None:
    with pytest.raises(ValueError, match="non-empty 'name' and 'command'"):
        MCPServerConfig.from_mapping({"name": "", "command": "python"})


@pytest.mark.asyncio
async def test_provider_discovers_and_invokes_stdio_tool() -> None:
    config = MCPServerConfig(name="echo-server", command=sys.executable, args=[str(_ECHO_SERVER)])
    provider = MCPToolProvider([config])
    try:
        tools = await provider.connect()
        assert provider.tools == tools

        by_name = {tool.name: tool for tool in tools}
        assert "echo" in by_name

        echo_tool = by_name["echo"]
        # MCP tools are async; invoke via acall.
        result = await echo_tool.acall(value="ping")
        assert "echo: ping" in str(result)
    finally:
        await provider.aclose()
        assert provider.tools == []


@pytest.mark.asyncio
async def test_provider_no_configs_returns_empty() -> None:
    provider = MCPToolProvider([])
    assert await provider.connect() == []
    await provider.aclose()


@pytest.mark.asyncio
async def test_provider_async_context_manager() -> None:
    config = MCPServerConfig(name="echo-server", command=sys.executable, args=[str(_ECHO_SERVER)])
    async with MCPToolProvider([config]) as provider:
        assert any(tool.name == "echo" for tool in provider.tools)


@pytest.mark.asyncio
async def test_agent_runtime_mcp_reattach_replaces_previous_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.runtime.agent import runtime as runtime_mod
    from fleet_rlm.runtime.agent.runtime import AgentRuntime
    from fleet_rlm.runtime.tools import mcp_tools as mcp_tools_mod

    class FakeProvider:
        instances: list[FakeProvider] = []

        def __init__(self, configs: list[Any]) -> None:
            self.configs = configs
            self.closed = False
            FakeProvider.instances.append(self)

        async def connect(self) -> list[dspy.Tool]:
            return [_tool(f"mcp_{self.configs[0]}")]

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setattr(runtime_mod, "discover_tools", lambda: [_tool("base_tool")])
    monkeypatch.setattr(mcp_tools_mod, "MCPToolProvider", FakeProvider)

    rt = AgentRuntime(use_escalation=False)

    assert await rt.attach_mcp_tools(configs=["first"]) == ["mcp_first"]
    assert [tool.name for tool in rt.tools] == ["base_tool", "mcp_first"]

    assert await rt.attach_mcp_tools(configs=["second"]) == ["mcp_second"]

    assert FakeProvider.instances[0].closed is True
    assert FakeProvider.instances[1].closed is False
    assert [tool.name for tool in rt.tools] == ["base_tool", "mcp_second"]


@pytest.mark.asyncio
async def test_agent_runtime_ashutdown_closes_mcp_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.runtime.agent import runtime as runtime_mod
    from fleet_rlm.runtime.agent.runtime import AgentRuntime
    from fleet_rlm.runtime.tools import mcp_tools as mcp_tools_mod

    class FakeProvider:
        instances: list[FakeProvider] = []

        def __init__(self, configs: list[Any]) -> None:
            self.configs = configs
            self.closed = False
            FakeProvider.instances.append(self)

        async def connect(self) -> list[dspy.Tool]:
            return [_tool(f"mcp_{self.configs[0]}")]

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setattr(runtime_mod, "discover_tools", lambda: [_tool("base_tool")])
    monkeypatch.setattr(mcp_tools_mod, "MCPToolProvider", FakeProvider)

    rt = AgentRuntime(use_escalation=False)
    await rt.attach_mcp_tools(configs=["session"])

    await rt.ashutdown()

    assert FakeProvider.instances[0].closed is True
    assert [tool.name for tool in rt.tools] == ["base_tool"]
