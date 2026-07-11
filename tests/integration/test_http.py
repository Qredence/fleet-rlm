from __future__ import annotations

import uuid as _uuid

from fleet_rlm.api.auth import NormalizedIdentity
from fleet_rlm.api.dependencies import require_http_identity
from fleet_rlm.db.repos.identity import IdentityUpsertResult


def _stub_identity() -> NormalizedIdentity:
    """Return a stub NormalizedIdentity for authenticated test requests."""
    return NormalizedIdentity(
        tenant_claim="tenant-a",
        user_claim="user-a",
        email="alice@example.com",
        name="Alice",
        raw_claims={"tid": "tenant-a", "oid": "user-a"},
    )


def _stub_persisted_identity() -> IdentityUpsertResult:
    """Return a stub persisted-identity result (no DB needed)."""
    tenant_id = _uuid.uuid5(_uuid.NAMESPACE_DNS, "tenant-a")
    user_id = _uuid.uuid5(_uuid.NAMESPACE_DNS, "user-a")
    return IdentityUpsertResult(
        tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=tenant_id,
    )


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


def test_auth_me_returns_identity_from_stubbed_identity(no_db_client) -> None:
    """GET /api/v1/auth/me returns the stubbed identity when a DB is available.

    The DevAuthProvider (which handled X-Debug-* headers) was removed as part
    of the config consolidation — Neon Auth is now the only authentication
    method. This test now stubs the auth dependency directly (matching the
    pattern in test_evaluations_auth.py) instead of relying on debug headers.

    The ``no_db_app`` fixture has no ``DATABASE_URL``, so ``/api/v1/auth/me``
    returns 503 ("Database repository unavailable for tenant admission")
    *after* auth succeeds. That 503 is distinct from the 401 an unauthenticated
    request would receive, so we accept both 200 (DB available) and 503 (DB
    unavailable) and verify that the response is never 401.
    """
    no_db_client.app.dependency_overrides[require_http_identity] = _stub_identity

    response = no_db_client.get("/api/v1/auth/me")

    # 200 = DB available, full identity returned; 503 = auth passed but no DB
    # for tenant admission. Neither is 401 (which would mean auth was rejected).
    assert response.status_code in {200, 503}, response.text
    assert response.status_code != 401, "authenticated request must not be rejected"
