from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.server.config import ServerRuntimeConfig
from fleet_rlm.server.main import create_app
from tests.ui.fixtures_ui import apply_ui_test_env


@pytest.fixture(autouse=True)
def _ui_server_state_isolation(reset_server_state):
    """Ensure per-test isolation for mutable global server state."""
    _ = reset_server_state
    yield


@pytest.fixture(autouse=True)
def _stub_server_lm_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep UI/server tests deterministic and fast by skipping env LM loading."""
    apply_ui_test_env(monkeypatch, tmp_path)


@pytest.fixture
def default_client() -> TestClient:
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def local_client() -> TestClient:
    app = create_app(
        config=ServerRuntimeConfig(
            app_env="local",
            database_required=False,
        )
    )
    with TestClient(app) as client:
        yield client


@pytest.fixture
def staging_client() -> TestClient:
    app = create_app(
        config=ServerRuntimeConfig(
            app_env="staging",
            database_required=False,
            auth_required=True,
            allow_debug_auth=False,
            allow_query_auth_tokens=False,
            cors_allowed_origins=["https://example.com"],
            dev_jwt_secret="staging-test-secret",
        )
    )
    with TestClient(app) as client:
        yield client
