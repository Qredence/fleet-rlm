from __future__ import annotations


def test_health_endpoint_returns_canonical_shape(no_db_client) -> None:
    response = no_db_client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "live"
    assert "version" in response.json()


def test_not_found_returns_structured_404(no_db_client) -> None:
    response = no_db_client.get("/definitely-missing-route")

    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found",
        "message": "Not Found",
        "detail": None,
    }


def test_auth_me_returns_identity_from_debug_headers(no_db_client, auth_headers: dict[str, str]) -> None:
    response = no_db_client.get("/api/v1/auth/me", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "tenant_claim": auth_headers["X-Debug-Tenant-Id"],
        "user_claim": auth_headers["X-Debug-User-Id"],
        "email": auth_headers["X-Debug-Email"],
        "name": auth_headers["X-Debug-Name"],
        "tenant_id": None,
        "user_id": None,
    }
