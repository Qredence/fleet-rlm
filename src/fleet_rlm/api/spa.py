"""Frontend asset and root-route mounting helpers for the FastAPI app."""

from __future__ import annotations

import asyncio
from importlib import import_module
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


def _resolve_ui_web_root(candidate: Path) -> Path | None:
    """Return the directory that contains the served SPA entrypoint, if any."""
    candidate = candidate.resolve()
    nested_client_root = candidate / "client"
    if (nested_client_root / "index.html").is_file():
        return nested_client_root
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


def mount_spa(app: FastAPI, ui_dir: Path) -> None:
    """Mount built frontend assets and SPA fallback route.

    MUST be called after all API routers are registered on ``app``. The
    reserved-path set used by the catch-all is derived from ``app.routes`` at
    mount time.
    """
    # Safety: catching a misordered call early, before it masks real bugs.
    if not any(getattr(r, "path", "").startswith("/api/") for r in app.routes):
        msg = "mount_spa must be called after API routes are registered"
        raise RuntimeError(msg)

    assets_dir = ui_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
    branding_dir = ui_dir / "branding"
    if branding_dir.exists():
        app.mount("/branding", StaticFiles(directory=str(branding_dir)), name="branding")

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

        return JSONResponse(ui_unavailable_payload(), status_code=503)


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
