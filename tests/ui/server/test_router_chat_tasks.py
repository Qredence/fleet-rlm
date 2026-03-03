from __future__ import annotations

from fastapi.testclient import TestClient


def test_removed_chat_route_returns_404_or_405(
    default_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = default_client.post(
        "/api/v1/chat",
        json={"message": "hello", "trace": False},
        headers=auth_headers,
    )
    assert response.status_code in {404, 405}
    assert "Deprecation" not in response.headers
    assert "X-Fleet-Removal-Version" not in response.headers


def test_removed_chat_route_is_not_in_openapi(local_client: TestClient) -> None:
    assert "/api/v1/chat" not in set(local_client.app.openapi().get("paths", {}))
