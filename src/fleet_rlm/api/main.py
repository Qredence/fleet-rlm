"""FastAPI application factory with lifespan and Scalar docs."""

from __future__ import annotations

import asyncio
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
    memory,
    optimization,
    runs,
    runtime,
    sandboxes,
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
    sandboxes.router,
    runs.router,
    memory.router,
    optimization.router,
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
    """Register health and /api/v1 route groups on app.

    Must be called **before** ``_mount_spa`` so the SPA catch-all does not
    shadow API or docs paths.
    """
    app.include_router(health.router)

    api_router = APIRouter(prefix="/api/v1")
    for route_group in _CANONICAL_API_ROUTERS:
        api_router.include_router(route_group)
    app.include_router(api_router)


def _collect_reserved_top_level_paths(app: FastAPI) -> tuple[set[str], set[str]]:
    """Return (reserved_paths, reserved_prefixes) derived from mounted routes.

    Used by the SPA catch-all to avoid serving index.html for paths that
    correspond to real API, docs, or static-mount routes. Building this at
    mount time (rather than hardcoding) means new routers automatically
    become non-SPA paths.
    """
    reserved_paths: set[str] = set()
    reserved_prefixes: set[str] = set()

    for route in app.routes:
        raw_path = getattr(route, "path", None)
        if not raw_path or raw_path == "/":
            continue
        # Skip the SPA catch-all itself, once it has been registered.
        if raw_path == "/{full_path:path}":
            continue

        stripped = raw_path.lstrip("/")
        # Paths with a dynamic segment (e.g. "/api/v1/sessions/{session_id}")
        # become a prefix rule for their static leading segment.
        first_segment = stripped.split("/", 1)[0]
        if "{" in first_segment:
            continue
        if "{" in stripped:
            reserved_prefixes.add(f"{first_segment}/")
            continue
        reserved_paths.add(stripped)
        reserved_prefixes.add(f"{first_segment}/")

    return reserved_paths, reserved_prefixes


def _mount_spa(app: FastAPI, ui_dir: Path) -> None:
    """Mount built frontend assets and SPA fallback route.

    MUST be called **after** all API routers are registered on ``app``.
    The reserved-path set used by the catch-all is derived from
    ``app.routes`` at mount time.
    """
    # Safety: catching a misordered call early, before it masks real bugs.
    assert any(getattr(r, "path", "").startswith("/api/") for r in app.routes), (
        "_mount_spa must be called after API routes are registered"
    )

    assets_dir = ui_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
    branding_dir = ui_dir / "branding"
    if branding_dir.exists():
        app.mount(
            "/branding", StaticFiles(directory=str(branding_dir)), name="branding"
        )

    ui_root = ui_dir.resolve()
    index_path = ui_root / "index.html"
    # Cached at mount time. If the index is deleted after boot that is an
    # operational issue, not a request-path concern.
    index_exists = index_path.is_file()

    reserved_paths, reserved_prefixes = _collect_reserved_top_level_paths(app)

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
        if normalized_path in reserved_paths:
            return False
        for prefix in reserved_prefixes:
            if normalized_path.startswith(prefix):
                return False
        # Only serve the SPA index for extensionless paths (client-side routes).
        return Path(normalized_path).suffix == ""

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        requested_file = await asyncio.to_thread(resolve_ui_file, full_path)
        if requested_file is not None:
            return FileResponse(requested_file)

        if index_exists and should_serve_spa_index(full_path):
            return FileResponse(index_path)

        if index_exists:
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


def _mount_api_only_root(app: FastAPI) -> None:
    """Expose a minimal JSON banner at `/` for API-only deploys."""

    @app.get("/", include_in_schema=False)
    async def api_only_root():
        banner: dict[str, Any] = {
            "name": app.title,
            "version": app.version,
        }
        if app.docs_url:
            banner["docs"] = app.docs_url
        if app.openapi_url:
            banner["openapi"] = app.openapi_url
        return JSONResponse(banner)


def _annotate_validation_error_schemas(app: FastAPI) -> None:
    """Fill FastAPI-generated validation schemas with property descriptions.

    Results are cached via ``app.openapi_schema`` so the schema walk runs
    exactly once per app lifetime, matching the FastAPI docs guidance at
    https://fastapi.tiangolo.com/how-to/extending-openapi/#cache-the-generated-schema.
    """

    original_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

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

        app.openapi_schema = schema
        return schema

    app.openapi = cast(Any, custom_openapi)


def create_app(*, config: ServerRuntimeConfig | None = None) -> FastAPI:
    """Create the FastAPI application instance."""
    cfg = resolve_runtime_config(config)

    cfg.validate_startup_or_raise()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state = build_server_state(cfg)
        app.state.server_state = state
        app.state.config_deps = state.config_deps
        app.state.lm_deps = state.lm_deps
        app.state.auth_deps = state.auth_deps
        app.state.session_cache_deps = state.session_cache_deps
        app.state.persistence_deps = state.persistence_deps
        app.state.diagnostics_deps = state.diagnostics_deps
        await startup_server_state(state)
        # Recover optimization runs orphaned by prior server restart
        try:
            if state.persistence_deps.repository is not None:
                recovered = await state.persistence_deps.repository.recover_stale_optimization_runs()
            else:
                from fleet_rlm.integrations.local_store import (
                    recover_stale_optimization_runs,
                )

                recovered = recover_stale_optimization_runs()
            if recovered:
                logger.info(
                    "Recovered %d stale optimization run(s) on startup", recovered
                )
        except Exception:
            logger.warning(
                "Stale optimization run recovery failed; some runs may remain in "
                "'running' state",
                exc_info=True,
            )
        yield
        await shutdown_server_state(state)

    app = FastAPI(
        title="fleet-rlm",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if cfg.expose_docs else None,
        redoc_url="/redoc" if cfg.expose_docs else None,
        openapi_url="/openapi.json" if cfg.expose_docs else None,
    )
    _annotate_validation_error_schemas(app)

    add_middlewares(app, cfg)
    _register_api_routes(app)

    if cfg.expose_docs:
        try:
            scalar_fastapi = cast(Any, import_module("scalar_fastapi"))
            get_scalar_api_reference = scalar_fastapi.get_scalar_api_reference

            @app.get("/scalar", include_in_schema=False)
            def scalar_docs():
                return get_scalar_api_reference(
                    openapi_url=app.openapi_url,
                    title=app.title,
                )
        except ImportError as exc:
            logger.warning(
                "scalar_fastapi not installed; /scalar docs endpoint disabled: %s",
                exc,
            )

    if cfg.serve_ui:
        ui_dir = _resolve_ui_dist_dir()
        if ui_dir is not None:
            _mount_spa(app, ui_dir)
        else:
            _mount_ui_unavailable_root(app)
    elif cfg.expose_root:
        _mount_api_only_root(app)

    return app


app = create_app()
