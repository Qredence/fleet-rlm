"""Serve commands for FastAPI servers.

This module implements the serve-api command used by the parent CLI entrypoint.
"""

from __future__ import annotations

from importlib.util import find_spec

import typer


def serve_api_command(
    host: str | None = typer.Option(None, help="Bind host (defaults to config.yaml)"),
    port: int | None = typer.Option(None, help="Bind port (defaults to config.yaml)"),
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

    from fleet_rlm.api.bootstrap import resolve_runtime_config
    from fleet_rlm.api.main import create_app
    from fleet_rlm.cli.config import get_current_app_config, get_current_config_overrides
    from fleet_rlm.integrations.config.process import load_process_config, server_config_values

    # The application factory resolves canonical YAML and environment aliases.
    overrides = get_current_config_overrides()
    server_config = None
    if overrides:
        base = resolve_runtime_config()
        resolution = load_process_config(overrides=overrides)
        override_paths = {path for path, source in resolution.sources.items() if source == "override"}
        projected = server_config_values(resolution.config, include_paths=override_paths)
        server_config = type(base).model_validate({**base.model_dump(), **projected})
    app_obj = create_app(config=server_config)
    process = get_current_app_config() or load_process_config().config
    uvicorn.run(app_obj, host=host or process.api.host, port=port or process.api.port)
