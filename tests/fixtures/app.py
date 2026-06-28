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

    # Stub recover_stale_optimization_runs before main.py imports it.
    # The synchronous SQLite session in recover_local_stale_runs() blocks
    # the TestClient portal thread in CI environments (120 s timeout).
    # Unit tests never populate the optimization-run state table, so
    # skipping recovery is safe.
    from fleet_rlm.api import bootstrap as _bootstrap

    async def _noop_recovery(_state):  # noqa: ARG001
        return

    monkeypatch.setattr(
        _bootstrap,
        "recover_stale_optimization_runs",
        _noop_recovery,
    )

    from fleet_rlm.api.config import AppConfig
    from fleet_rlm.api.main import create_app

    app = create_app(
        config=AppConfig(
            app_env="local",
            auth_required=False,
            database_required=False,
            database_url=None,  # ty: ignore[unknown-argument] — populate_by_name=True lets callers use the Python field name; ty doesn't model this
            db_validate_on_startup=False,
            serve_ui=False,  # ty: ignore[unknown-argument]
            expose_root=False,  # ty: ignore[unknown-argument]
            interpreter_pool_size=0,
            interpreter_pool_overflow_max=0,
        )
    )
    return app


@pytest.fixture
def no_db_client(no_db_app) -> Iterator[TestClient]:
    """TestClient bound to the no-DB app."""
    with TestClient(no_db_app) as client:
        yield client
