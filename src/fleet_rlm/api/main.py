"""FastAPI application factory with lifespan and Scalar docs."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from fleet_rlm import __version__

from .bootstrap import (
    build_server_state,
    resolve_runtime_config,
    shutdown_server_state,
    startup_server_state,
)
from .config import ServerRuntimeConfig
from .middleware import add_middlewares
from .routers import (
    auth,
    health,
    runtime,
    sessions,
    traces,
    ws,
)

logger = logging.getLogger(__name__)


def _resolve_ui_dist_dir() -> Path | None:
    """Return the frontend build directory if one exists.

    In source checkouts, prefer `src/frontend/dist` so `fleet web` reflects the
    latest local frontend build. For installed packages, fall back to in-package
    assets at `fleet_rlm/ui/dist`.
    """
    repo_root = Path(__file__).resolve().parents[3]
    candidates = [
        repo_root / "src" / "frontend" / "dist",  # current repo layout
        Path(__file__).parent.parent / "ui" / "dist",  # packaged fallback
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _register_api_routes(app: FastAPI) -> None:
    """Register health and /api/v1 route groups on app."""
    app.include_router(health.router)

    api_router = APIRouter(prefix="/api/v1")
    api_router.include_router(auth.router)
    api_router.include_router(ws.router)
    api_router.include_router(sessions.router)
    api_router.include_router(runtime.router)
    api_router.include_router(traces.router)
    app.include_router(api_router)


def _mount_spa(app: FastAPI, ui_dir: Path) -> None:
    """Mount built frontend assets and SPA fallback route."""
    assets_dir = ui_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
    branding_dir = ui_dir / "branding"
    if branding_dir.exists():
        app.mount(
            "/branding", StaticFiles(directory=str(branding_dir)), name="branding"
        )

    ui_root = ui_dir.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        _ = full_path
        index_path = ui_root / "index.html"
        if index_path.exists():
            return FileResponse(index_path)

        return {"error": "UI build not found. Run 'pnpm run build' in src/frontend."}


def create_app(*, config: ServerRuntimeConfig | None = None) -> FastAPI:
    cfg = resolve_runtime_config(config)

    cfg.validate_startup_or_raise()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state = build_server_state(cfg)
        app.state.server_state = state
        await startup_server_state(state, cfg)
        yield
        await shutdown_server_state(state)

    app = FastAPI(
        title="fleet-rlm",
        version=__version__,
        lifespan=lifespan,
    )

    add_middlewares(app, cfg)
    _register_api_routes(app)

    try:
        get_scalar_api_reference: Any = import_module(
            "scalar_fastapi"
        ).get_scalar_api_reference

        @app.get("/scalar", include_in_schema=False)
        async def scalar_docs():
            return get_scalar_api_reference(
                openapi_url=app.openapi_url,
                title=app.title,
            )
    except ImportError:
        # Scalar docs are optional and only enabled when scalar_fastapi is installed.
        pass

    ui_dir = _resolve_ui_dist_dir()
    if ui_dir is not None:
        _mount_spa(app, ui_dir)

    return app


app = create_app()
