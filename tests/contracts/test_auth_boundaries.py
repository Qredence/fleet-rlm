from __future__ import annotations

import pytest

from fleet_rlm.api.auth import NormalizedIdentity
from fleet_rlm.api.dependencies import require_http_identity


def _stub_identity() -> NormalizedIdentity:
    """Return a stub NormalizedIdentity for authenticated test requests."""
    return NormalizedIdentity(
        tenant_claim="tenant-a",
        user_claim="user-a",
        email="alice@example.com",
        name="Alice",
        raw_claims={"tid": "tenant-a", "oid": "user-a"},
    )


@pytest.mark.parametrize("path", ["/api/v1/auth/me", "/api/v1/sessions/state"])
def test_auth_boundaries_reject_missing_credentials(no_db_client, path: str) -> None:
    no_db_client.app.state.config_deps.config.auth_required = True

    response = no_db_client.get(path)

    assert response.status_code in {401, 403}


@pytest.mark.parametrize("path", ["/api/v1/auth/me", "/api/v1/sessions/state"])
def test_auth_boundaries_accept_authenticated_request_in_local_mode(
    no_db_client,
    path: str,
) -> None:
    """Authenticated requests with a stubbed identity reach the handler.

    The DevAuthProvider (which handled X-Debug-* headers) was removed as part
    of the config consolidation — Neon Auth is now the only authentication
    method. These tests now stub the auth dependency directly (matching the
    pattern in test_evaluations_auth.py) instead of relying on debug headers.

    For ``/api/v1/auth/me``, the handler additionally requires a
    ``FleetRepository`` for tenant admission; when no DB is available (the
    ``no_db_app`` fixture), the handler returns 503 *after* auth succeeds.
    That 503 is distinct from the 401 an unauthenticated request would get,
    so we accept both 200 (DB available) and 503 (DB unavailable) here.
    """
    no_db_client.app.state.config_deps.config.auth_required = True
    no_db_client.app.dependency_overrides[require_http_identity] = _stub_identity

    response = no_db_client.get(path)

    # 200 = handler fully processed; 503 = auth passed but DB unavailable for
    # tenant admission (only for /api/v1/auth/me). Neither is 401 (which would
    # mean auth was rejected).
    if path == "/api/v1/auth/me":
        assert response.status_code in {200, 503}, response.text
    else:
        assert response.status_code == 200, response.text
    assert response.status_code != 401, "authenticated request must not be rejected"
