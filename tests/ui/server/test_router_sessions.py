from fastapi.testclient import TestClient

from fleet_rlm.server.main import create_app

AUTH_HEADERS = {
    "X-Debug-Tenant-Id": "tenant-a",
    "X-Debug-User-Id": "user-a",
    "X-Debug-Email": "alice@example.com",
    "X-Debug-Name": "Alice",
}


def test_sessions_state_endpoint_exists():
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/sessions/state", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert isinstance(body["sessions"], list)
