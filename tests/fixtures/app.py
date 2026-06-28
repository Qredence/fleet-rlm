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

    # Stub the heavy lifespan hooks BEFORE main.create_app() runs.
    # main.py imports these with `from ... import ...`, binding them in
    # main's namespace where the lifespan closure captures them.
    #
    # recover_stale_optimization_runs: the synchronous SQLite session in
    #   recover_local_stale_runs() blocks the TestClient portal thread in
    #   CI (120 s timeout).  Unit tests never populate the state table.
    # startup_server_state: interpreter pool warm-up, optional service
    #   scheduling (MLflow / PostHog / LLM model loading), and profile
    #   repair are all unnecessary for no-database unit tests and can
    #   compete with the TestClient portal thread in CI.
    #   Persistence initialization (LocalStore) is still performed so
    #   tests depending on persistence_deps.local_store continue to work.
    from fleet_rlm.api import main as _main

    async def _noop_recovery(_state):
        return

    async def _minimal_startup(state):
        from fleet_rlm.integrations.local_store import LocalStore

        state.persistence_deps.local_store = LocalStore()

    async def _noop_shutdown(_state):
        return

    monkeypatch.setattr(_main, "startup_server_state", _minimal_startup)
    monkeypatch.setattr(_main, "recover_stale_optimization_runs", _noop_recovery)
    monkeypatch.setattr(_main, "shutdown_server_state", _noop_shutdown)

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
