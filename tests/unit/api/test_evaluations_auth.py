"""Auth boundary tests for the evaluation endpoints (VAL-SEC-001, VAL-SEC-002, VAL-SEC-003).

These tests verify that all three evaluation endpoints
(POST /api/v1/evaluations, GET /api/v1/evaluations,
GET /api/v1/evaluations/{run_id}) reject unauthenticated requests with 401
before the route handler executes, and accept authenticated requests.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.dependencies import (
    HTTPIdentityDep,
    PersistedIdentityDep,
    require_http_identity,
    resolve_persisted_identity,
)
from fleet_rlm.api.runtime_services import evaluations as evaluation_service
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.quality.eval.report import EvaluationReport


def _stub_identity() -> object:
    """Return a stub NormalizedIdentity for authenticated test requests."""
    from fleet_rlm.api.auth import NormalizedIdentity

    return NormalizedIdentity(
        tenant_claim="tenant-a",
        user_claim="user-a",
        email="alice@example.com",
        name="Alice",
        raw_claims={"tid": "tenant-a", "oid": "user-a"},
    )


def _stub_persisted_identity() -> IdentityUpsertResult:
    """Return a stub persisted-identity result for authenticated test requests."""
    import uuid as _uuid

    tenant_id = _uuid.uuid5(_uuid.NAMESPACE_DNS, "tenant-a")
    user_id = _uuid.uuid5(_uuid.NAMESPACE_DNS, "user-a")
    return IdentityUpsertResult(
        tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=tenant_id,
    )


@pytest.fixture
def evaluations_client(no_db_app, monkeypatch) -> Iterator[TestClient]:
    """Client with the eval auth dependencies overridden to a stub identity.

    For the 401 cases we flip ``auth_required=True`` and clear the overrides so
    the real Neon auth dependency rejects the missing token. For the 200 cases
    we install stub overrides so the request reaches the handler without
    touching the Neon JWKS endpoint.

    The background ``asyncio.create_task`` scheduled by ``start_evaluation_run``
    is stubbed to a no-op that immediately closes the coroutine (so no
    ``asyncio.to_thread`` worker thread is spawned). This lets the auth tests
    (which only assert the 401/200 boundary) avoid leaving a lingering worker
    thread that would hang the TestClient portal shutdown. The non-blocking
    behavior is verified separately in ``test_evaluations_background.py``.
    """
    from joserfc.jwk import KeySet

    from fleet_rlm.api.auth.neon import NeonAuthProvider

    monkeypatch.setattr(NeonAuthProvider, "_fetch_jwks", lambda self: KeySet([]))

    app = no_db_app
    app.dependency_overrides[require_http_identity] = _stub_identity  # type: ignore[assignment]
    app.dependency_overrides[resolve_persisted_identity] = _stub_persisted_identity  # type: ignore[assignment]
    evaluation_service._EVALUATION_STORE.clear()

    def _noop_create_task(coro, *_args, **_kwargs):
        # Close the coroutine without running it so no background work is
        # scheduled and no worker thread is spawned.
        coro.close()
        loop = asyncio.get_event_loop()
        return loop.create_task(asyncio.sleep(0))  # return a real Task object

    with TestClient(app) as client:
        with patch.object(evaluation_service.asyncio, "create_task", side_effect=_noop_create_task):
            yield client
    evaluation_service._EVALUATION_STORE.clear()
    evaluation_service._INFLIGHT_TASKS.clear()


def _require_auth(client: TestClient) -> None:
    """Flip the app to require real auth and clear identity overrides."""
    client.app.state.config_deps.config.auth_required = True
    client.app.dependency_overrides.pop(require_http_identity, None)
    client.app.dependency_overrides.pop(resolve_persisted_identity, None)


def test_post_evaluations_rejects_missing_token(evaluations_client: TestClient) -> None:
    """VAL-SEC-001: POST without a Bearer token returns 401."""
    _require_auth(evaluations_client)

    response = evaluations_client.post(
        "/api/v1/evaluations",
        json={"from_last_days": 1},
    )

    assert response.status_code == 401, response.text


def test_post_evaluations_rejects_invalid_token(evaluations_client: TestClient) -> None:
    """VAL-SEC-001: POST with an invalid Bearer token returns 401."""
    _require_auth(evaluations_client)

    response = evaluations_client.post(
        "/api/v1/evaluations",
        json={"from_last_days": 1},
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert response.status_code == 401, response.text


def test_post_evaluations_accepts_authenticated_request(evaluations_client: TestClient) -> None:
    """VAL-SEC-001: POST with a valid (stubbed) identity reaches the handler.

    With background-task conversion (VAL-SEC-009), POST returns immediately
    with a generated run_id and ``status="pending"``; the actual evaluation is
    scheduled via ``asyncio.create_task`` and never blocks the response. We
    patch ``run_evaluation`` so the background task (if it runs) returns a
    known report, but we only assert the immediate POST response shape.
    """
    run_id = str(uuid4())
    fake_report = EvaluationReport(
        run_id=run_id,
        created_at="2026-01-01T00:00:00+00:00",
        filters={"trace_ids": None, "limit": None, "from_last_days": 1},
        per_trace=[],
        aggregates={"mean": {}, "median": {}},
    )
    evaluation_service._EVALUATION_STORE.clear()
    evaluations_client.app.state.config_deps.config.auth_required = False

    with patch.object(evaluation_service, "run_evaluation", return_value=fake_report):
        response = evaluations_client.post(
            "/api/v1/evaluations",
            json={"from_last_days": 1},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "run_id" in body
    assert body["status"] == "pending"


def test_get_evaluations_list_rejects_missing_token(evaluations_client: TestClient) -> None:
    """VAL-SEC-002: GET list without a Bearer token returns 401."""
    _require_auth(evaluations_client)

    response = evaluations_client.get("/api/v1/evaluations")

    assert response.status_code == 401, response.text


def test_get_evaluations_list_rejects_invalid_token(evaluations_client: TestClient) -> None:
    """VAL-SEC-002: GET list with an invalid Bearer token returns 401."""
    _require_auth(evaluations_client)

    response = evaluations_client.get(
        "/api/v1/evaluations",
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert response.status_code == 401, response.text


def test_get_evaluations_list_accepts_authenticated_request(evaluations_client: TestClient) -> None:
    """VAL-SEC-002: GET list with a valid identity returns 200 with a ``runs`` key."""
    evaluation_service._EVALUATION_STORE.clear()
    evaluations_client.app.state.config_deps.config.auth_required = False

    response = evaluations_client.get("/api/v1/evaluations")

    assert response.status_code == 200, response.text
    assert "runs" in response.json()


def test_get_evaluation_by_id_rejects_missing_token(evaluations_client: TestClient) -> None:
    """VAL-SEC-003: GET {run_id} without a Bearer token returns 401."""
    _require_auth(evaluations_client)
    run_id = "00000000-0000-0000-0000-000000000000"

    response = evaluations_client.get(f"/api/v1/evaluations/{run_id}")

    assert response.status_code == 401, response.text


def test_get_evaluation_by_id_rejects_invalid_token(evaluations_client: TestClient) -> None:
    """VAL-SEC-003: GET {run_id} with an invalid Bearer token returns 401."""
    _require_auth(evaluations_client)
    run_id = "00000000-0000-0000-0000-000000000000"

    response = evaluations_client.get(
        f"/api/v1/evaluations/{run_id}",
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert response.status_code == 401, response.text


def test_get_evaluation_by_id_returns_404_for_unknown_run_when_authenticated(
    evaluations_client: TestClient,
) -> None:
    """VAL-SEC-003: authenticated GET for a non-existent run returns 404 (not 401)."""
    evaluation_service._EVALUATION_STORE.clear()
    evaluations_client.app.state.config_deps.config.auth_required = False
    run_id = "00000000-0000-0000-0000-000000000000"

    response = evaluations_client.get(f"/api/v1/evaluations/{run_id}")

    assert response.status_code == 404, response.text


# Ensure the dependency type aliases are importable (validates wiring).
_ = (HTTPIdentityDep, PersistedIdentityDep)
