"""FastAPI application factory with lifespan and Scalar docs."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
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

_CANONICAL_API_ROUTERS = (
    auth.router,
    ws.router,
    sessions.router,
    runtime.router,
    traces.router,
)


_VALIDATION_ERROR_PROPERTY_DESCRIPTIONS: dict[str, str] = {
    "detail": "Structured list of request validation issues returned by FastAPI.",
    "loc": "Location path identifying where the validation error occurred.",
    "msg": "Human-readable validation failure message.",
    "type": "Pydantic validation error type identifier.",
    "input": "Input value that failed validation, when available.",
    "ctx": "Optional structured validation context for templated error messages.",
}


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
    for route_group in _CANONICAL_API_ROUTERS:
        api_router.include_router(route_group)
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

    def resolve_ui_file(full_path: str) -> Path | None:
        requested_path = (ui_root / full_path).resolve(strict=False)
        try:
            requested_path.relative_to(ui_root)
        except ValueError:
            return None
        return requested_path if requested_path.is_file() else None

    def should_serve_spa_index(full_path: str) -> bool:
        normalized_path = full_path.strip("/")
        if normalized_path == "":
            return True

        reserved_prefixes = ("api/", "docs/", "redoc/", "scalar/")
        reserved_paths = {
            "api",
            "docs",
            "health",
            "openapi.json",
            "ready",
            "redoc",
            "scalar",
        }
        if normalized_path in reserved_paths:
            return False
        if normalized_path.startswith(reserved_prefixes):
            return False
        return Path(normalized_path).suffix == ""

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        requested_file = resolve_ui_file(full_path)
        if requested_file is not None:
            return FileResponse(requested_file)

        index_path = ui_root / "index.html"
        if index_path.exists() and should_serve_spa_index(full_path):
            return FileResponse(index_path)

        if index_path.exists():
            raise HTTPException(status_code=404, detail="Not Found")

        return JSONResponse(_ui_unavailable_payload(), status_code=503)


def _ui_unavailable_payload() -> dict[str, str]:
    """Return a source-aware hint when the web UI bundle is unavailable."""
    repo_root = Path(__file__).resolve().parents[3]
    frontend_root = repo_root / "src" / "frontend"

    if (frontend_root / "package.json").exists():
        return {
            "error": "UI build not found.",
            "hint": (
                "Build the frontend with "
                "'cd src/frontend && pnpm install --frozen-lockfile && pnpm run build' "
                "and sync packaged UI assets with 'make build-ui' before rebuilding."
            ),
        }

    return {
        "error": "Packaged UI assets are missing from this installation.",
        "hint": (
            "Reinstall a wheel or sdist built with synced frontend assets, or use a "
            "newer fleet-rlm release."
        ),
    }


def _mount_ui_unavailable_root(app: FastAPI) -> None:
    """Expose a helpful root response when the UI bundle is unavailable."""

    @app.get("/", include_in_schema=False)
    async def ui_unavailable_root():
        return JSONResponse(_ui_unavailable_payload(), status_code=503)


def _annotate_validation_error_schemas(app: FastAPI) -> None:
    """Fill FastAPI-generated validation schemas with property descriptions."""

    original_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        schema = original_openapi()
        components = schema.get("components", {}).get("schemas", {})

        for schema_name in ("HTTPValidationError", "ValidationError"):
            properties = components.get(schema_name, {}).get("properties", {})
            for (
                property_name,
                description,
            ) in _VALIDATION_ERROR_PROPERTY_DESCRIPTIONS.items():
                if property_name in properties and not properties[property_name].get(
                    "description"
                ):
                    properties[property_name]["description"] = description

        return schema

    app.openapi = cast(Any, custom_openapi)


def create_app(*, config: ServerRuntimeConfig | None = None) -> FastAPI:
    cfg = resolve_runtime_config(config)

    cfg.validate_startup_or_raise()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state = build_server_state(cfg)
        app.state.server_state = state
        await startup_server_state(state)
        yield
        await shutdown_server_state(state)

    app = FastAPI(
        title="fleet-rlm",
        version=__version__,
        lifespan=lifespan,
    )
    _annotate_validation_error_schemas(app)

    add_middlewares(app, cfg)
    _register_api_routes(app)

    try:
        scalar_fastapi = cast(Any, import_module("scalar_fastapi"))
        get_scalar_api_reference = scalar_fastapi.get_scalar_api_reference

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
    else:
        _mount_ui_unavailable_root(app)

    return app


app = create_app()
