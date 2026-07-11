"""Input validation tests for EvaluationRequest (VAL-SEC-006, VAL-SEC-007, VAL-SEC-008).

These tests verify that the ``limit``, ``from_last_days``, and ``trace_ids``
fields on ``EvaluationRequest`` reject out-of-range values with 422 and accept
in-range values (which may then return 200/503 depending on MLflow).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.dependencies import (
    require_http_identity,
    resolve_persisted_identity,
)
from fleet_rlm.api.runtime_services import evaluations as evaluation_service
from fleet_rlm.db.repos.identity import IdentityUpsertResult
from fleet_rlm.quality.eval.report import EvaluationReport


def _stub_identity() -> object:
    from fleet_rlm.api.auth import NormalizedIdentity

    return NormalizedIdentity(
        tenant_claim="tenant-a",
        user_claim="user-a",
        email="alice@example.com",
        name="Alice",
        raw_claims={"tid": "tenant-a", "oid": "user-a"},
    )


def _stub_persisted_identity() -> IdentityUpsertResult:
    import uuid as _uuid

    tenant_id = _uuid.uuid5(_uuid.NAMESPACE_DNS, "tenant-a")
    user_id = _uuid.uuid5(_uuid.NAMESPACE_DNS, "user-a")
    return IdentityUpsertResult(
        tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=tenant_id,
    )


@pytest.fixture
def evaluations_client(no_db_app) -> Iterator[TestClient]:
    """Client with auth dependencies stubbed so validation is the only gate.

    The background ``asyncio.create_task`` scheduled by ``start_evaluation_run``
    is stubbed to a no-op (the coroutine is closed without running) so the
    validation tests (which only assert 422/200 boundary behavior) do not
    leave a lingering ``asyncio.to_thread`` worker thread that would hang the
    TestClient portal shutdown. Non-blocking behavior is verified in
    ``test_evaluations_background.py``.
    """
    app = no_db_app
    app.dependency_overrides[require_http_identity] = _stub_identity  # type: ignore[assignment]
    app.dependency_overrides[resolve_persisted_identity] = _stub_persisted_identity  # type: ignore[assignment]
    evaluation_service._EVALUATION_STORE.clear()

    def _noop_create_task(coro, *_args, **_kwargs):
        coro.close()
        loop = asyncio.get_event_loop()
        return loop.create_task(asyncio.sleep(0))

    with patch.object(evaluation_service.asyncio, "create_task", side_effect=_noop_create_task):
        with TestClient(app) as client:
            yield client
    evaluation_service._EVALUATION_STORE.clear()
    evaluation_service._INFLIGHT_TASKS.clear()


def _patch_run_evaluation(run_id: str):
    """Patch run_evaluation so in-range requests return 200 instead of hitting MLflow.

    With background-task conversion (VAL-SEC-009), POST returns a freshly
    generated run_id with ``status="pending"``; the patched ``run_evaluation``
    only matters if the background task actually runs. We assert the POST
    response shape (200 + status=pending), not the patched report's run_id.
    """
    fake_report = EvaluationReport(
        run_id=run_id,
        created_at="2026-01-01T00:00:00+00:00",
        filters={"trace_ids": None, "limit": None, "from_last_days": 1},
        per_trace=[],
        aggregates={"mean": {}, "median": {}},
    )
    return patch.object(evaluation_service, "run_evaluation", return_value=fake_report)


# ---------------------------------------------------------------------------
# VAL-SEC-006: limit range (ge=1, le=1000)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("limit", [0, -1, -5, 1001, 5000])
def test_post_evaluations_rejects_out_of_range_limit(
    evaluations_client: TestClient,
    limit: int,
) -> None:
    """VAL-SEC-006: out-of-range limit returns 422."""
    response = evaluations_client.post(
        "/api/v1/evaluations",
        json={"limit": limit},
    )

    assert response.status_code == 422, response.text
    body = response.json()
    # The validation error should mention the limit field.
    assert "limit" in response.text.lower(), body


@pytest.mark.parametrize("limit", [1, 1000, 50])
def test_post_evaluations_accepts_in_range_limit(
    evaluations_client: TestClient,
    limit: int,
) -> None:
    """VAL-SEC-006: in-range limit is accepted (not 422)."""
    run_id = str(uuid4())
    with _patch_run_evaluation(run_id):
        response = evaluations_client.post(
            "/api/v1/evaluations",
            json={"limit": limit},
        )

    assert response.status_code != 422, response.text
    assert response.status_code == 200
    body = response.json()
    assert "run_id" in body
    assert body["status"] == "pending"


# ---------------------------------------------------------------------------
# VAL-SEC-007: from_last_days range (ge=0, le=365)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("days", [-1, -10, 366, 400])
def test_post_evaluations_rejects_out_of_range_from_last_days(
    evaluations_client: TestClient,
    days: int,
) -> None:
    """VAL-SEC-007: out-of-range from_last_days returns 422."""
    response = evaluations_client.post(
        "/api/v1/evaluations",
        json={"from_last_days": days},
    )

    assert response.status_code == 422, response.text
    assert "from_last_days" in response.text.lower()


@pytest.mark.parametrize("days", [0, 1, 365])
def test_post_evaluations_accepts_in_range_from_last_days(
    evaluations_client: TestClient,
    days: int,
) -> None:
    """VAL-SEC-007: in-range from_last_days is accepted (not 422)."""
    run_id = str(uuid4())
    with _patch_run_evaluation(run_id):
        response = evaluations_client.post(
            "/api/v1/evaluations",
            json={"from_last_days": days},
        )

    assert response.status_code != 422, response.text
    assert response.status_code == 200
    body = response.json()
    assert "run_id" in body
    assert body["status"] == "pending"


# ---------------------------------------------------------------------------
# VAL-SEC-008: trace_ids max_length=100
# ---------------------------------------------------------------------------


def test_post_evaluations_rejects_too_many_trace_ids(evaluations_client: TestClient) -> None:
    """VAL-SEC-008: 101 trace_ids returns 422."""
    trace_ids = [f"id-{i}" for i in range(101)]

    response = evaluations_client.post(
        "/api/v1/evaluations",
        json={"trace_ids": trace_ids},
    )

    assert response.status_code == 422, response.text
    assert "trace_ids" in response.text.lower()


def test_post_evaluations_accepts_max_trace_ids(evaluations_client: TestClient) -> None:
    """VAL-SEC-008: exactly 100 trace_ids is accepted (not 422)."""
    run_id = str(uuid4())
    trace_ids = [f"id-{i}" for i in range(100)]
    with _patch_run_evaluation(run_id):
        response = evaluations_client.post(
            "/api/v1/evaluations",
            json={"trace_ids": trace_ids},
        )

    assert response.status_code != 422, response.text
    assert response.status_code == 200
    body = response.json()
    assert "run_id" in body
    assert body["status"] == "pending"


def test_post_evaluations_accepts_empty_trace_ids(evaluations_client: TestClient) -> None:
    """VAL-SEC-008: an empty trace_ids list is accepted."""
    run_id = str(uuid4())
    with _patch_run_evaluation(run_id):
        response = evaluations_client.post(
            "/api/v1/evaluations",
            json={"trace_ids": []},
        )

    assert response.status_code != 422, response.text
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


def test_post_evaluations_accepts_null_trace_ids(evaluations_client: TestClient) -> None:
    """VAL-SEC-008: a null/omitted trace_ids field is accepted."""
    run_id = str(uuid4())
    with _patch_run_evaluation(run_id):
        response = evaluations_client.post(
            "/api/v1/evaluations",
            json={},
        )

    assert response.status_code != 422, response.text
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
