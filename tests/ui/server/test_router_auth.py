from fastapi.testclient import TestClient

from fleet_rlm.server.config import ServerRuntimeConfig
from fleet_rlm.server.main import create_app


def test_auth_me_is_optional_in_dev_by_default():
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/auth/me")
        assert response.status_code == 200
        body = response.json()
        assert body["tenant_claim"] == "default"
        assert body["user_claim"] == "anonymous"


def test_auth_me_requires_auth_when_configured():
    app = create_app(config=ServerRuntimeConfig(auth_mode="dev", auth_required=True))
    with TestClient(app) as client:
        response = client.get("/auth/me")
        assert response.status_code == 401


def test_auth_me_returns_normalized_identity():
    app = create_app()
    with TestClient(app) as client:
        response = client.get(
            "/auth/me",
            headers={
                "X-Debug-Tenant-Id": "tenant-1",
                "X-Debug-User-Id": "user-1",
                "X-Debug-Email": "user@example.com",
                "X-Debug-Name": "User One",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["tenant_claim"] == "tenant-1"
        assert body["user_claim"] == "user-1"
        assert body["email"] == "user@example.com"
        assert body["name"] == "User One"
