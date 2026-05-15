"""Tests for FastAPI frontend/root route mounting helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fleet_rlm.api.spa import mount_api_only_root, mount_frontend_routes, mount_spa


def test_mount_api_only_root_returns_server_banner() -> None:
    """Root route returns the API-only banner payload when UI serving is disabled."""
    app = FastAPI(title="fleet-rlm", version="test", docs_url="/docs", openapi_url="/openapi.json")
    mount_api_only_root(app)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "name": "fleet-rlm",
        "version": "test",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


def test_mount_frontend_routes_skips_root_when_api_only_root_disabled() -> None:
    """Frontend mounting leaves `/` unbound when both UI and API-only root are disabled."""
    app = FastAPI(title="fleet-rlm", version="test")
    mount_frontend_routes(app, serve_ui=False, expose_root=False)

    assert "/" not in {getattr(route, "path", "") for route in app.routes}


def test_mount_spa_serves_client_routes_without_shadowing_api(tmp_path: Path) -> None:
    """SPA fallback serves client routes while preserving API and static-file behavior."""
    ui_dir = tmp_path / "dist"
    ui_dir.mkdir()
    (ui_dir / "index.html").write_text("<html>fleet ui</html>", encoding="utf-8")
    (ui_dir / "robots.txt").write_text("User-agent: *\n", encoding="utf-8")

    app = FastAPI()

    @app.get("/api/v1/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    mount_spa(app, ui_dir)

    with TestClient(app) as client:
        api_response = client.get("/api/v1/ping")
        client_route_response = client.get("/workspace")
        static_file_response = client.get("/robots.txt")
        missing_api_response = client.get("/api/v1/missing")

    assert api_response.status_code == 200
    assert api_response.json() == {"status": "ok"}
    assert client_route_response.status_code == 200
    assert "fleet ui" in client_route_response.text
    assert static_file_response.status_code == 200
    assert static_file_response.text == "User-agent: *\n"
    assert missing_api_response.status_code == 404


def test_mount_spa_requires_api_routes_registered(tmp_path) -> None:
    """SPA mounting fails fast when called before API routes are registered."""
    ui_dir = tmp_path / "dist"
    ui_dir.mkdir()
    (ui_dir / "index.html").write_text("<html>fleet ui</html>", encoding="utf-8")

    app = FastAPI()
    with pytest.raises(RuntimeError, match="after API routes are registered"):
        mount_spa(app, ui_dir)
