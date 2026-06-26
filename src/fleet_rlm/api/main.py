"""FastAPI application factory with lifespan and Scalar docs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from fleet_rlm import __version__
from fleet_rlm.utils import install_log_redaction_filters

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
from .errors import add_exception_handlers
from .middleware import add_middlewares
from .openapi import annotate_validation_error_schemas
from .routers import (
    auth,
    health,
    info,
    optimization,
    runs,
    runtime,
    sandboxes,
    sessions,
    traces,
    ws,
)
from .spa import mount_frontend_routes


def _register_api_routes(app: FastAPI) -> None:
    """Register health and /api/v1 route groups on app.

    Must be called before ``mount_spa`` so the SPA catch-all does not
    shadow API or docs paths.

    Inclusion order is intentional — FastAPI resolves routes in registration
    order, so changing this list can alter matching behaviour for overlapping
    paths.
    """
    app.include_router(health.router)

    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(auth.router)
    api_v1.include_router(info.router)
    api_v1.include_router(ws.router)
    api_v1.include_router(sessions.router)
    api_v1.include_router(runtime.router)
    from .routers import llm_profiles

    api_v1.include_router(llm_profiles.router)
    api_v1.include_router(sandboxes.router)
    api_v1.include_router(runs.router)
    api_v1.include_router(optimization.router)
    api_v1.include_router(traces.router)
    from .routers import evaluations

    api_v1.include_router(evaluations.router)
    app.include_router(api_v1)


def create_app(*, config: ServerRuntimeConfig | None = None) -> FastAPI:
    """Create the FastAPI application instance."""
    install_log_redaction_filters()
    cfg = resolve_runtime_config(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg.validate_startup_or_raise()
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

    add_exception_handlers(app)
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
