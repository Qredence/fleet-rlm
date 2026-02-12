import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from fleet_rlm.server.main import create_app

    app = create_app()
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


def test_request_id_header(client):
    r = client.get("/health")
    assert "X-Request-ID" in r.headers
