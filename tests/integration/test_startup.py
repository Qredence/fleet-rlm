from __future__ import annotations

from fastapi.testclient import TestClient


def test_app_starts_and_stops_cleanly_without_database(no_db_app) -> None:
    with TestClient(no_db_app) as client:
        assert client.app.state.persistence_deps.db_manager is None
        assert client.app.state.persistence_deps.local_store is not None


def test_health_is_reachable_during_startup_lifespan(no_db_app) -> None:
    with TestClient(no_db_app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "live"
