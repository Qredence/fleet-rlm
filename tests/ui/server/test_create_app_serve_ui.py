"""Tests for `create_app` serve_ui gating (API-only vs SPA-serving)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from fleet_rlm import __version__
from fleet_rlm.api.config import ServerRuntimeConfig
from fleet_rlm.api.main import create_app


def _base_cfg(**overrides) -> ServerRuntimeConfig:
    defaults = dict(
        app_env="local",
        database_required=False,
        database_url=None,
        db_validate_on_startup=False,
    )
    defaults.update(overrides)
    return ServerRuntimeConfig(**defaults)


def test_api_only_root_returns_json_banner_when_serve_ui_false() -> None:
    app = create_app(config=_base_cfg(serve_ui=False))
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "fleet-rlm"
    assert payload["version"] == __version__
    assert payload["docs"] == "/docs"
    assert payload["openapi"] == "/openapi.json"


def test_api_only_mode_does_not_mount_assets_route() -> None:
    app = create_app(config=_base_cfg(serve_ui=False))

    mounted_paths = {getattr(route, "path", None) for route in app.routes}
    # Neither the SPA catch-all nor the /assets mount should exist when
    # the frontend is deployed separately.
    assert "/assets" not in mounted_paths
    assert "/branding" not in mounted_paths
    assert "/{full_path:path}" not in mounted_paths


def test_api_only_root_is_hidden_when_expose_root_false() -> None:
    app = create_app(
        config=_base_cfg(
            app_env="production",
            serve_ui=False,
            expose_root=False,
            auth_required=True,
            dev_jwt_secret="prod-secret",
            allow_debug_auth=False,
            allow_query_auth_tokens=False,
            cors_allowed_origins=["https://example.com"],
        )
    )
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 404


def test_docs_are_hidden_when_expose_docs_false() -> None:
    app = create_app(
        config=_base_cfg(
            app_env="production",
            serve_ui=False,
            expose_docs=False,
            auth_required=True,
            dev_jwt_secret="prod-secret",
            allow_debug_auth=False,
            allow_query_auth_tokens=False,
            cors_allowed_origins=["https://example.com"],
        )
    )
    client = TestClient(app)

    response = client.get("/docs")

    assert response.status_code == 404


def test_ready_stays_public_when_auth_is_required() -> None:
    app = create_app(
        config=_base_cfg(
            app_env="staging",
            auth_required=True,
            allow_debug_auth=False,
            allow_query_auth_tokens=False,
            dev_jwt_secret="staging-secret",
            cors_allowed_origins=["https://example.com"],
        )
    )
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200


def test_serve_ui_true_preserves_health_and_docs_endpoints() -> None:
    # `serve_ui=True` with no dist falls back to the 503-JSON root handler,
    # but health/docs must still be reachable.
    app = create_app(config=_base_cfg(serve_ui=True))
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    # FastAPI serves /docs as HTML; we just care that routing reaches it.
    assert client.get("/docs").status_code == 200
