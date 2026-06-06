"""Minimal stdio MCP server used by MCP tool-provider tests.

Run as ``python <this file>``; exposes a single ``echo`` tool over stdio so the
runtime's :class:`MCPToolProvider` can be exercised end-to-end without a network.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

server = FastMCP("fleet-rlm-test-mcp")


@server.tool()
def echo(value: str) -> str:
    """Return the input value prefixed with ``echo:`` for assertions."""
    return f"echo: {value}"


if __name__ == "__main__":
    server.run()
