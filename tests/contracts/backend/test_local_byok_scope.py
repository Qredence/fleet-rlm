"""QRE-79 local BYOK scope contracts at the public HTTP boundary."""

from __future__ import annotations

from fastapi.testclient import TestClient

from fleet_rlm.composition.testing import create_testing_app


def test_session_and_turn_creation_need_no_identity_or_authorization_headers() -> None:
    app = create_testing_app()

    with TestClient(app) as client:
        created = client.post("/api/sessions", json={"title": "Local chat"})
        assert created.status_code == 201

        response = client.post(
            f"/api/sessions/{created.json()['id']}/turns",
            json={"text": "hello"},
            headers={"Idempotency-Key": "local-byok-turn"},
        )

    assert response.status_code == 200


def test_openapi_does_not_publish_authentication_or_identity_headers() -> None:
    schema = create_testing_app().openapi()
    serialized = str(schema).lower()

    assert "authorization" not in serialized
    assert "x-fleet-user-id" not in serialized
    assert "x-fleet-workspace-id" not in serialized
    assert "securityschemes" not in serialized
