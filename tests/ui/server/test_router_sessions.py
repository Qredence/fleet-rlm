from fastapi.testclient import TestClient

from fleet_rlm.server.main import create_app


def test_sessions_state_endpoint_exists():
    app = create_app()
    client = TestClient(app)
    resp = client.get("/sessions/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["sessions"], list)
