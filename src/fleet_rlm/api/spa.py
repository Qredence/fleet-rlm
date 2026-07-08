"""Frontend asset and root-route mounting helpers for the FastAPI app."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse


def _resolve_ui_web_root(candidate: Path) -> Path | None:
    """Return the directory that contains the served SPA entrypoint, if any."""
    candidate = candidate.resolve()
    nested_client_root = candidate / "client"
    if nested_client_root.is_dir():
        # TanStack Start writes fresh browser assets under dist/client. If that
        # tree exists without an entrypoint, do not fall back to a stale legacy
        # dist/index.html from an older build.
        if (nested_client_root / "index.html").is_file():
            return nested_client_root
        return None
    if (candidate / "index.html").is_file():
        return candidate
    return None


def _fleet_ui_package_root() -> Path | None:
    """Return the installed/source `fleet_rlm.ui` package directory when available."""
    module = import_module("fleet_rlm.ui")
    module_file = getattr(module, "__file__", None)
    if not module_file:
        return None
    return Path(module_file).resolve().parent


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def is_source_frontend_checkout() -> bool:
    """Return True when this process is running from a source tree with ``src/frontend``."""
    return (_repo_root() / "src" / "frontend" / "package.json").is_file()


def _source_frontend_dist_dir() -> Path:
    ui_package_root = _fleet_ui_package_root()
    if ui_package_root is None:
        return _repo_root() / "src" / "frontend" / "dist"
    return ui_package_root.parents[1] / "frontend" / "dist"


def resolve_ui_dist_dir() -> Path | None:
    """Return the frontend build directory that contains the served SPA files.

    In source checkouts, only ``src/frontend/dist`` is considered so ``fleet web``
    never serves stale packaged assets from ``fleet_rlm/ui/dist``. For installed
    packages, fall back to in-package assets at ``fleet_rlm/ui/dist``.
    """
    if is_source_frontend_checkout():
        resolved = _resolve_ui_web_root(_source_frontend_dist_dir())
        if resolved is not None:
            return resolved
        return None

    ui_package_root = _fleet_ui_package_root()
    if ui_package_root is None:
        return None
    return _resolve_ui_web_root(ui_package_root / "dist")


def _join_route_path(prefix: str, path: str) -> str:
    joined = f"{prefix.rstrip('/')}/{path.lstrip('/')}"
    return joined if joined.startswith("/") else f"/{joined}"


def _iter_route_paths(routes: Any, *, prefix: str = ""):
    """Yield concrete paths from FastAPI's flat or nested route structures."""
    for route in routes:
        raw_path = getattr(route, "path", None)
        if raw_path:
            yield _join_route_path(prefix, raw_path)
            continue

        original_router = getattr(route, "original_router", None)
        nested_routes = getattr(original_router, "routes", None)
        include_context = getattr(route, "include_context", None)
        include_prefix = str(getattr(include_context, "prefix", "") or "")
        if nested_routes is not None:
            yield from _iter_route_paths(nested_routes, prefix=_join_route_path(prefix, include_prefix))


def mount_spa(app: FastAPI, ui_dir: Path) -> None:
    """Mount built frontend assets using FastAPI's native frontend helper.

    MUST be called after all API routers are registered on ``app`` so normal
    routes are matched before the low-priority frontend fallback.
    """
    # Safety: catching a misordered call early, before it masks real bugs.
    if not any(path.startswith("/api/") for path in _iter_route_paths(app.routes)):
        msg = "mount_spa must be called after API routes are registered"
        raise RuntimeError(msg)

    app.frontend("/", directory=ui_dir)


def ui_unavailable_payload() -> dict[str, str]:
    """Return a source-aware hint when the web UI bundle is unavailable."""
    frontend_root = _repo_root() / "src" / "frontend"

    if (frontend_root / "package.json").exists():
        return {
            "error": "UI build not found.",
            "hint": (
                "Build the frontend with "
                "'cd src/frontend && pnpm install --frozen-lockfile && pnpm run build' "
                "to serve it from fleet web on :8000, or run "
                "'cd src/frontend && pnpm run dev' for HMR on :5173 with API proxy to :8000."
            ),
        }

    return {
        "error": "Packaged UI assets are missing from this installation.",
        "hint": ("Reinstall a wheel or sdist built with synced frontend assets, or use a newer fleet-rlm release."),
    }


def mount_ui_unavailable_root(app: FastAPI) -> None:
    """Expose a helpful root response when the UI bundle is unavailable."""

    @app.get("/", include_in_schema=False)
    async def ui_unavailable_root():
        return JSONResponse(ui_unavailable_payload(), status_code=503)


def mount_api_only_root(app: FastAPI) -> None:
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


def mount_frontend_routes(
    app: FastAPI,
    *,
    serve_ui: bool,
    expose_root: bool,
) -> None:
    """Mount the configured UI or root route surface on ``app``."""
    if serve_ui:
        ui_dir = resolve_ui_dist_dir()
        if ui_dir is not None:
            mount_spa(app, ui_dir)
        else:
            mount_ui_unavailable_root(app)
        return

    if expose_root:
        mount_api_only_root(app)


__all__ = [
    "is_source_frontend_checkout",
    "mount_api_only_root",
    "mount_frontend_routes",
    "mount_spa",
    "mount_ui_unavailable_root",
    "resolve_ui_dist_dir",
    "ui_unavailable_payload",
]
