"""HTTP contract for session-bound human feedback on MLflow execution traces."""

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

from fleet_rlm.api.dependencies import get_mlflow_runtime, get_trace_feedback_service
from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.observability.feedback import (
    TraceFeedbackNotFoundError,
    TraceFeedbackResult,
    TraceFeedbackUnavailableError,
)


class _FeedbackService:
    def __init__(self, result: TraceFeedbackResult | None = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, object]] = []

    def submit(self, **kwargs: object) -> TraceFeedbackResult:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class _MlflowRuntime:
    error: BaseException | None = None

    async def run_operation(self, function, *args: object, **kwargs: object):
        if self.error is not None:
            raise self.error
        return function(*args, **kwargs)


def _app(service: _FeedbackService):
    app = create_testing_app()
    app.dependency_overrides[get_trace_feedback_service] = lambda: service
    app.dependency_overrides[get_mlflow_runtime] = _MlflowRuntime
    return app


def _session(client: TestClient) -> UUID:
    response = client.post("/api/sessions", json={})
    assert response.status_code == 201
    return UUID(response.json()["id"])


def test_feedback_route_returns_safe_assessment_projection_and_forwards_scope() -> None:
    service = _FeedbackService(TraceFeedbackResult("tr-execution", True, "assessment-1"))
    app = _app(service)
    with TestClient(app) as client:
        session_id = _session(client)
        response = client.post(
            f"/api/sessions/{session_id}/traces/feedback",
            json={"trace_id": "tr-execution", "value": True, "comment": " useful "},
        )

    assert response.status_code == 200
    assert response.json() == {
        "trace_id": "tr-execution",
        "name": "user_feedback",
        "value": True,
        "assessment_id": "assessment-1",
    }
    assert service.calls[0]["session_id"] == session_id
    assert service.calls[0]["trace_id"] == "tr-execution"
    assert service.calls[0]["comment"] == " useful "


def test_feedback_route_maps_trace_mismatch_and_backend_failure_to_closed_errors() -> None:
    missing = _FeedbackService(error=TraceFeedbackNotFoundError())
    app = _app(missing)
    with TestClient(app) as client:
        session_id = _session(client)
        response = client.post(
            f"/api/sessions/{session_id}/traces/feedback",
            json={"trace_id": "tr-mismatch", "value": False},
        )
    assert response.status_code == 404
    assert response.json() == {"code": "feedback_trace_not_found", "message": "Trace not found"}

    unavailable = _FeedbackService(error=TraceFeedbackUnavailableError())
    app = _app(unavailable)
    with TestClient(app) as client:
        session_id = _session(client)
        response = client.post(
            f"/api/sessions/{session_id}/traces/feedback",
            json={"trace_id": "tr-backend", "value": True},
        )
    assert response.status_code == 503
    assert response.json() == {
        "code": "trace_feedback_unavailable",
        "message": "Trace feedback is unavailable",
    }


def test_feedback_route_maps_closed_mlflow_lifecycle_to_unavailable() -> None:
    service = _FeedbackService(TraceFeedbackResult("tr-closed", True, None))
    app = _app(service)
    runtime = _MlflowRuntime()
    runtime.error = RuntimeError("internal lifecycle detail")
    app.dependency_overrides[get_mlflow_runtime] = lambda: runtime
    with TestClient(app) as client:
        session_id = _session(client)
        response = client.post(
            f"/api/sessions/{session_id}/traces/feedback",
            json={"trace_id": "tr-closed", "value": True},
        )

    assert response.status_code == 503
    assert response.json() == {
        "code": "trace_feedback_unavailable",
        "message": "Trace feedback is unavailable",
    }


def test_feedback_route_rejects_invalid_bodies_and_unknown_sessions() -> None:
    service = _FeedbackService(TraceFeedbackResult("tr", True, None))
    app = _app(service)
    with TestClient(app) as client:
        invalid = client.post(
            "/api/sessions/00000000-0000-0000-0000-000000000001/traces/feedback",
            json={"trace_id": "   ", "value": "true", "extra": "reject"},
        )
        unknown = client.post(
            "/api/sessions/00000000-0000-0000-0000-000000000002/traces/feedback",
            json={"trace_id": "tr-unknown", "value": True},
        )

    assert invalid.status_code == 422
    assert unknown.status_code == 404
    assert service.calls == []
