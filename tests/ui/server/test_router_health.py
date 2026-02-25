from fastapi.testclient import TestClient


def test_health(local_client: TestClient):
    r = local_client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True


def test_ready_no_planner(local_client: TestClient):
    from fleet_rlm.server.deps import server_state

    server_state.planner_lm = None
    r = local_client.get("/ready")
    assert r.status_code == 200
    data = r.json()
    assert data["ready"] is False
    assert data["planner"] == "missing"
    assert data["database"] in {"disabled", "missing", "ready"}
    assert "sandbox_provider" in data


def test_request_id_header(local_client: TestClient):
    r = local_client.get("/health")
    assert "X-Request-ID" in r.headers
