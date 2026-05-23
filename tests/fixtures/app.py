"""App factory fixtures for tests that need a FastAPI TestClient."""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def no_db_app(monkeypatch):
    """FastAPI app with no database — avoids DB-ping timeout."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_ADMIN_URL", raising=False)
    monkeypatch.setenv("POSTHOG_ENABLED", "false")
    monkeypatch.setenv("MLFLOW_ENABLED", "false")

    from fleet_rlm.api.config import ServerRuntimeConfig
    from fleet_rlm.api.main import create_app

    app = create_app(
        config=ServerRuntimeConfig(
            app_env="local",
            database_required=False,
            database_url=None,
            db_validate_on_startup=False,
            serve_ui=False,
            expose_root=False,
        )
    )
    return app


@pytest.fixture
def no_db_client(no_db_app) -> Iterator[TestClient]:
    """TestClient bound to the no-DB app."""
    with TestClient(no_db_app) as client:
        yield client
