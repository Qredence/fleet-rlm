"""HTTP contract for Fleet liveness and readiness probes."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

import fleet_rlm
from fleet_rlm.composition.inventory import RuntimeDatabaseLifecycle
from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.config.settings import Settings
from fleet_rlm.persistence.database import create_async_engine_from_url


def _settings(tmp_path: Path, database_url: str | None) -> Settings:
    return Settings(
        run_environment="daytona",
        data_root=str(tmp_path / "data"),
        database_url=database_url,
    )


def test_liveness_answers_before_composition_and_reports_identity(tmp_path: Path) -> None:
    app = create_testing_app(settings=_settings(tmp_path, None))
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app": "fleet-rlm",
        "version": fleet_rlm.__version__,
    }


def test_readiness_is_closed_503_before_composition(tmp_path: Path) -> None:
    app = create_testing_app(settings=_settings(tmp_path, None))
    client = TestClient(app)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"code": "service_not_ready", "message": "Service is not ready"}


def test_readiness_reports_database_ok_when_composed(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'readiness.db').resolve()}"
    app = create_testing_app(settings=_settings(tmp_path, database_url))

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}


def test_readiness_reports_not_configured_without_a_database(tmp_path: Path) -> None:
    app = create_testing_app(settings=_settings(tmp_path, None))

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "not_configured"}


def test_readiness_is_closed_503_when_the_configured_database_is_unreachable(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'readiness.db').resolve()}"
    app = create_testing_app(settings=_settings(tmp_path, database_url))
    broken_engine = create_async_engine_from_url(
        f"sqlite+aiosqlite:///{(tmp_path / 'missing' / 'broken.db').resolve()}"
    )

    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200

        inventory = app.state.runtime_inventory
        assert inventory is not None
        app.state.runtime_inventory = replace(
            inventory,
            database=RuntimeDatabaseLifecycle(engine=broken_engine),
        )

        degraded = client.get("/health/ready")

    assert degraded.status_code == 503
    assert degraded.json() == {"code": "service_not_ready", "message": "Service is not ready"}


@pytest.mark.asyncio
async def test_readiness_probe_is_bounded_against_a_hung_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RuntimeDatabaseLifecycle, "_PROBE_TIMEOUT_SECONDS", 0.05)

    class HungConnection:
        async def __aenter__(self) -> HungConnection:
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            del exc_info

        async def execute(self, statement: object) -> None:
            del statement
            await asyncio.sleep(30)

    class HungEngine:
        def connect(self) -> HungConnection:
            return HungConnection()

    lifecycle = RuntimeDatabaseLifecycle(engine=cast(Any, HungEngine()))
    started = time.monotonic()

    verdict = await lifecycle.readiness()

    assert verdict == "unreachable"
    assert time.monotonic() - started < 5
