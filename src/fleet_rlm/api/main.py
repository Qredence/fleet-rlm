"""FastAPI application factory with lifespan and Scalar docs."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from fleet_rlm import __version__

from .bootstrap import (
    attach_server_state,
    build_server_state,
    recover_stale_optimization_runs,
    resolve_runtime_config,
    shutdown_server_state,
    startup_server_state,
)
from .config import ServerRuntimeConfig
from .docs import mount_scalar_docs
from .middleware import add_middlewares
from .openapi import annotate_validation_error_schemas
from .routers import health
from .routers._composition import build_api_router
from .spa import mount_frontend_routes


def _register_api_routes(app: FastAPI) -> None:
    """Register health and /api/v1 route groups on app.

    Must be called before ``mount_spa`` so the SPA catch-all does not
    shadow API or docs paths.
    """
    app.include_router(health.router)
    app.include_router(build_api_router())


def create_app(*, config: ServerRuntimeConfig | None = None) -> FastAPI:
    """Create the FastAPI application instance."""
    cfg = resolve_runtime_config(config)

    cfg.validate_startup_or_raise()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = build_server_state(cfg)
        attach_server_state(app, state)
        try:
            await startup_server_state(state)
            await recover_stale_optimization_runs(state)
            yield
        finally:
            await shutdown_server_state(state)

    app = FastAPI(
        title="fleet-rlm",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if cfg.expose_docs else None,
        redoc_url="/redoc" if cfg.expose_docs else None,
        openapi_url="/openapi.json" if cfg.expose_docs else None,
    )
    annotate_validation_error_schemas(app)

    add_middlewares(app, cfg)
    _register_api_routes(app)

    if cfg.expose_docs:
        mount_scalar_docs(app)

    mount_frontend_routes(
        app,
        serve_ui=cfg.serve_ui,
        expose_root=cfg.expose_root,
    )

    return app


app = create_app()
