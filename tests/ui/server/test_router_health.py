import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from fleet_rlm.server.main import create_app
    from fleet_rlm.server.config import ServerRuntimeConfig

    app = create_app(
        config=ServerRuntimeConfig(
            app_env="local",
            database_required=False,
            enable_legacy_sqlite_routes=False,
        )
    )
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True


def test_ready_no_planner(client):
    r = client.get("/ready")
    assert r.status_code == 200
    data = r.json()
    assert data["ready"] is False
    assert data["planner"] == "missing"
    assert data["database"] in {"disabled", "missing"}
    assert "sandbox_provider" in data


def test_request_id_header(client):
    r = client.get("/health")
    assert "X-Request-ID" in r.headers
