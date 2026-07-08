"""Unit tests for SPA asset resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fleet_rlm.api import spa


def test_is_source_frontend_checkout_true_in_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.setattr(spa, "_repo_root", lambda: repo_root)
    assert spa.is_source_frontend_checkout() is True


def test_resolve_ui_dist_dir_source_checkout_ignores_packaged_dist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    frontend_dist = repo_root / "src" / "frontend" / "dist"
    packaged_dist = repo_root / "src" / "fleet_rlm" / "ui" / "dist"
    frontend_dist.mkdir(parents=True)
    packaged_dist.mkdir(parents=True)
    (frontend_dist / "package.json").touch()
    (repo_root / "src" / "frontend" / "package.json").write_text("{}", encoding="utf-8")
    (packaged_dist / "index.html").write_text("<html>stale</html>", encoding="utf-8")

    monkeypatch.setattr(spa, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(spa, "is_source_frontend_checkout", lambda: True)
    monkeypatch.setattr(spa, "_source_frontend_dist_dir", lambda: frontend_dist)

    assert spa.resolve_ui_dist_dir() is None


def test_resolve_ui_dist_dir_source_checkout_prefers_frontend_dist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    frontend_dist = repo_root / "src" / "frontend" / "dist"
    packaged_dist = repo_root / "src" / "fleet_rlm" / "ui" / "dist"
    frontend_dist.mkdir(parents=True)
    packaged_dist.mkdir(parents=True)
    (frontend_dist / "index.html").write_text("<html>fresh</html>", encoding="utf-8")
    (packaged_dist / "index.html").write_text("<html>stale</html>", encoding="utf-8")

    monkeypatch.setattr(spa, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(spa, "is_source_frontend_checkout", lambda: True)
    monkeypatch.setattr(spa, "_source_frontend_dist_dir", lambda: frontend_dist)

    resolved = spa.resolve_ui_dist_dir()
    assert resolved is not None
    assert resolved.name == "dist"
    assert (resolved / "index.html").read_text(encoding="utf-8") == "<html>fresh</html>"


def test_resolve_ui_dist_dir_source_checkout_prefers_nested_client_dist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    frontend_dist = repo_root / "src" / "frontend" / "dist"
    client_dist = frontend_dist / "client"
    client_dist.mkdir(parents=True)
    (frontend_dist / "index.html").write_text("<html>stale</html>", encoding="utf-8")
    (client_dist / "index.html").write_text("<html>fresh client</html>", encoding="utf-8")

    monkeypatch.setattr(spa, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(spa, "is_source_frontend_checkout", lambda: True)
    monkeypatch.setattr(spa, "_source_frontend_dist_dir", lambda: frontend_dist)

    resolved = spa.resolve_ui_dist_dir()
    assert resolved == client_dist.resolve()
    assert (resolved / "index.html").read_text(encoding="utf-8") == "<html>fresh client</html>"


def test_resolve_ui_dist_dir_source_checkout_rejects_stale_root_when_client_dist_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    frontend_dist = repo_root / "src" / "frontend" / "dist"
    client_dist = frontend_dist / "client"
    client_dist.mkdir(parents=True)
    (frontend_dist / "index.html").write_text("<html>stale</html>", encoding="utf-8")

    monkeypatch.setattr(spa, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(spa, "is_source_frontend_checkout", lambda: True)
    monkeypatch.setattr(spa, "_source_frontend_dist_dir", lambda: frontend_dist)

    assert spa.resolve_ui_dist_dir() is None


def test_resolve_ui_dist_dir_installed_package_uses_packaged_dist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packaged_dist = tmp_path / "fleet_rlm" / "ui" / "dist"
    packaged_dist.mkdir(parents=True)
    (packaged_dist / "index.html").write_text("<html>wheel</html>", encoding="utf-8")

    monkeypatch.setattr(spa, "is_source_frontend_checkout", lambda: False)
    monkeypatch.setattr(spa, "_fleet_ui_package_root", lambda: packaged_dist.parent)

    resolved = spa.resolve_ui_dist_dir()
    assert resolved is not None
    assert (resolved / "index.html").read_text(encoding="utf-8") == "<html>wheel</html>"


def test_ui_unavailable_payload_mentions_dev_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    frontend_root = repo_root / "src" / "frontend"
    frontend_root.mkdir(parents=True)
    (frontend_root / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(spa, "_repo_root", lambda: repo_root)

    payload = spa.ui_unavailable_payload()
    assert "pnpm run dev" in payload["hint"]
    assert ":5173" in payload["hint"]


def test_mount_spa_serves_client_route_without_shadowing_api(tmp_path: Path) -> None:
    ui_root = tmp_path / "dist" / "client"
    ui_root.mkdir(parents=True)
    (ui_root / "index.html").write_text("<html>fresh</html>", encoding="utf-8")

    app = FastAPI()

    @app.get("/api/health")
    async def api_health() -> dict[str, str]:
        return {"status": "ok"}

    spa.mount_spa(app, ui_root)
    client = TestClient(app)

    api_response = client.get("/api/health")
    assert api_response.status_code == 200
    assert api_response.json() == {"status": "ok"}

    app_response = client.get("/app/workspace")
    assert app_response.status_code == 200
    assert "fresh" in app_response.text


def test_mount_spa_serves_static_asset(tmp_path: Path) -> None:
    ui_root = tmp_path / "dist" / "client"
    assets_root = ui_root / "assets"
    assets_root.mkdir(parents=True)
    (ui_root / "index.html").write_text("<html>fresh</html>", encoding="utf-8")
    (assets_root / "app.js").write_text("console.log('ok')", encoding="utf-8")

    app = FastAPI()

    @app.get("/api/health")
    async def api_health() -> dict[str, str]:
        return {"status": "ok"}

    spa.mount_spa(app, ui_root)

    response = TestClient(app).get("/assets/app.js")

    assert response.status_code == 200
    assert "console.log" in response.text


def test_mount_spa_handles_entrypoint_deleted_after_startup(tmp_path: Path) -> None:
    ui_root = tmp_path / "dist" / "client"
    ui_root.mkdir(parents=True)
    index_path = ui_root / "index.html"
    index_path.write_text("<html>fresh</html>", encoding="utf-8")

    app = FastAPI()

    @app.get("/api/health")
    async def api_health() -> dict[str, str]:
        return {"status": "ok"}

    spa.mount_spa(app, ui_root)
    index_path.unlink()

    response = TestClient(app).get("/app/workspace")

    assert response.status_code == 404
