from pathlib import Path


def _patch_main_resolve(monkeypatch, tmp_path) -> None:
    from fleet_rlm.server import main as server_main

    def fake_resolve(self, *args, **kwargs):  # noqa: ARG001
        return Path(tmp_path / "repo" / "src" / "fleet_rlm" / "server" / "main.py")

    monkeypatch.setattr(server_main.Path, "resolve", fake_resolve)


def test_resolve_ui_dist_dir_prefers_frontend_when_both_exist(monkeypatch, tmp_path):
    from fleet_rlm.server import main as server_main

    _patch_main_resolve(monkeypatch, tmp_path)

    orig_exists = server_main.Path.exists

    def fake_exists(self: Path):
        path_str = str(self)
        # Both candidates exist; local source checkout should prefer frontend dist.
        if path_str.endswith("/ui/dist"):
            return True
        if path_str.endswith("/src/frontend/dist"):
            return True
        return orig_exists(self)

    monkeypatch.setattr(server_main.Path, "exists", fake_exists)

    resolved = server_main._resolve_ui_dist_dir()

    assert resolved is not None
    assert str(resolved).endswith("/src/frontend/dist")


def test_resolve_ui_dist_dir_falls_back_to_packaged_dist(monkeypatch, tmp_path):
    from fleet_rlm.server import main as server_main

    _patch_main_resolve(monkeypatch, tmp_path)

    orig_exists = server_main.Path.exists

    def fake_exists(self: Path):
        path_str = str(self)
        if path_str.endswith("/src/frontend/dist"):
            return False
        if path_str.endswith("/ui/dist"):
            return True
        return orig_exists(self)

    monkeypatch.setattr(server_main.Path, "exists", fake_exists)

    resolved = server_main._resolve_ui_dist_dir()

    assert resolved is not None
    assert str(resolved).endswith("/ui/dist")


def test_create_app_serves_spa_index_from_frontend_dist(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from fleet_rlm.server.main import create_app
    from fleet_rlm.server.config import ServerRuntimeConfig
    from fleet_rlm.server import main as server_main

    ui_dist = tmp_path / "src" / "frontend" / "dist"
    assets_dir = ui_dist / "assets"
    branding_dir = ui_dist / "branding"
    assets_dir.mkdir(parents=True)
    branding_dir.mkdir(parents=True)
    (ui_dist / "index.html").write_text("<html><body>Fleet UI</body></html>")
    (branding_dir / "logo-mark.svg").write_text("<svg>logo</svg>")

    monkeypatch.setattr(server_main, "_resolve_ui_dist_dir", lambda: ui_dist)

    app = create_app(
        config=ServerRuntimeConfig(
            app_env="local",
            database_required=False,
        )
    )
    client = TestClient(app)

    r = client.get("/")
    assert r.status_code == 200
    assert "Fleet UI" in r.text

    logo = client.get("/branding/logo-mark.svg")
    assert logo.status_code == 200
    assert "<svg>logo</svg>" == logo.text
