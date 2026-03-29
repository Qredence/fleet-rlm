"""Serve commands for FastAPI and FastMCP servers.

This module implements the serve-api and serve-mcp commands used by the parent
CLI entrypoint.
"""

from __future__ import annotations

from importlib.util import find_spec

import typer

from ..config import require_current_app_config
from ..runtime_factory import build_mcp_runtime_config, build_server_runtime_config


def serve_api_command(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
) -> None:
    """Run the FastAPI server surface (used by `fleet web`)."""
    config = require_current_app_config()
    missing = [pkg for pkg in ("fastapi", "uvicorn") if find_spec(pkg) is None]
    if missing:
        typer.echo(
            "Server dependencies missing: "
            + ", ".join(missing)
            + "\nInstall/upgrade with:\n  uv pip install -U fleet-rlm"
            + "\n(or from repo: uv sync --extra dev --extra server)",
            err=True,
        )
        raise typer.Exit(code=2)

    import uvicorn

    from fleet_rlm.api.main import create_app

    app_obj = create_app(config=build_server_runtime_config(config))
    uvicorn.run(app_obj, host=host, port=port)


def serve_mcp_command(
    transport: str = typer.Option(
        "stdio",
        help="FastMCP transport: stdio, sse, streamable-http",
    ),
    host: str = typer.Option("127.0.0.1", help="Host for HTTP transports"),
    port: int = typer.Option(8001, help="Port for HTTP transports"),
) -> None:
    """Run optional FastMCP server surface (requires `--extra mcp`)."""
    config = require_current_app_config()
    missing = [pkg for pkg in ("fastmcp",) if find_spec(pkg) is None]
    if missing:
        typer.echo(
            "MCP dependencies missing: "
            + ", ".join(missing)
            + "\nInstall with:\n  uv sync --extra dev --extra mcp",
            err=True,
        )
        raise typer.Exit(code=2)

    from fleet_rlm.integrations.mcp.server import (
        create_mcp_server,
    )

    server = create_mcp_server(config=build_mcp_runtime_config(config))

    transport_norm = transport.strip().lower()
    if transport_norm == "stdio":
        server.run(transport="stdio")
        return

    if transport_norm in {"sse", "streamable-http"}:
        server.run(transport=transport_norm, host=host, port=port)
        return

    typer.echo("transport must be one of: stdio, sse, streamable-http", err=True)
    raise typer.Exit(code=2)
