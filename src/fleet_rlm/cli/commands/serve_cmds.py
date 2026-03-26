"""Serve commands for FastAPI and FastMCP servers.

This module provides a registration function for serve-api and serve-mcp commands.
Commands are registered on the shared app instance from the parent CLI module.
"""

from __future__ import annotations

from importlib.util import find_spec
from typing import Any, Callable

import typer

from ..runtime_factory import build_mcp_runtime_config, build_server_runtime_config


def register_serve_commands(
    app: typer.Typer,
    *,
    get_config: Callable[[], Any],
    _handle_error: Callable[[Exception], None],
) -> None:
    """Register serve-api and serve-mcp CLI commands on *app*.

    Args:
        app: Typer app instance to register commands on.
        get_config: Function that returns the current AppConfig.
        _handle_error: Error handling callback.
    """

    @app.command("serve-api")
    def serve_api(
        host: str = typer.Option("127.0.0.1", help="Bind host"),
        port: int = typer.Option(8000, help="Bind port"),
    ) -> None:
        """Run the FastAPI server surface (used by `fleet web`)."""
        config = get_config()
        if config is None:
            raise typer.Exit(code=1)

        try:
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
        except Exception as exc:
            _handle_error(exc)

    @app.command("serve-mcp")
    def serve_mcp(
        transport: str = typer.Option(
            "stdio",
            help="FastMCP transport: stdio, sse, streamable-http",
        ),
        host: str = typer.Option("127.0.0.1", help="Host for HTTP transports"),
        port: int = typer.Option(8001, help="Port for HTTP transports"),
    ) -> None:
        """Run optional FastMCP server surface (requires `--extra mcp`)."""
        config = get_config()
        if config is None:
            raise typer.Exit(code=1)

        try:
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
            elif transport_norm in {"sse", "streamable-http"}:
                server.run(transport=transport_norm, host=host, port=port)
            else:
                typer.echo(
                    "transport must be one of: stdio, sse, streamable-http", err=True
                )
                raise typer.Exit(code=2)
        except Exception as exc:
            _handle_error(exc)
