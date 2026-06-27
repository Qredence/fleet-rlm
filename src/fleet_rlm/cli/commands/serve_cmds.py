"""Serve commands for FastAPI servers.

This module implements the serve-api command used by the parent CLI entrypoint.
"""

from __future__ import annotations

from importlib.util import find_spec

import typer


def serve_api_command(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
) -> None:
    """Run the FastAPI server surface (used by `fleet web`)."""
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

    # The server reads its configuration directly from environment variables
    # (AppConfig is a BaseSettings subclass). Hydra overrides applied to the
    # CLI's AppConfig (integrations/config/env.py) are mirrored into the
    # process environment before the server boots, so no explicit config
    # conversion is needed here.
    app_obj = create_app()
    uvicorn.run(app_obj, host=host, port=port)
