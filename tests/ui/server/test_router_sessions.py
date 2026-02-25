from fastapi.testclient import TestClient


def test_sessions_state_endpoint_exists(
    default_client: TestClient, auth_headers: dict[str, str]
):
    resp = default_client.get("/api/v1/sessions/state", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["sessions"], list)
