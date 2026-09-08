"""Session-bound MLflow trace assessment routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from fleet_rlm.api.dependencies import (
    LocalScopeDep,
    MLflowRuntimeDep,
    SessionCatalogDep,
    SettingsDep,
    TraceFeedbackServiceDep,
)
from fleet_rlm.api.errors import http_error
from fleet_rlm.api.schemas import TraceFeedbackRequest, TraceFeedbackResponse
from fleet_rlm.observability.feedback import (
    TraceFeedbackNotFoundError,
    TraceFeedbackUnavailableError,
)
from fleet_rlm.sessions.errors import SessionNotFoundError

router = APIRouter(prefix="/api/sessions", tags=["traces"])


@router.post(
    "/{session_id}/traces/feedback",
    response_model=TraceFeedbackResponse,
    operation_id="submit_trace_feedback",
)
async def submit_trace_feedback(
    session_id: UUID,
    body: TraceFeedbackRequest,
    identity: LocalScopeDep,
    repo: SessionCatalogDep,
    settings: SettingsDep,
    service: TraceFeedbackServiceDep,
    mlflow_runtime: MLflowRuntimeDep,
) -> TraceFeedbackResponse:
    """Record human feedback for an execution trace owned by this Session."""
    try:
        await repo.get(
            session_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
        )
    except SessionNotFoundError as exc:
        raise http_error(404, "feedback_trace_not_found", "Trace not found") from exc

    try:
        result = await mlflow_runtime.run_operation(
            service.submit,
            session_id=session_id,
            trace_id=body.trace_id,
            value=body.value,
            comment=body.comment,
            content_enabled=settings.mlflow_trace_content_enabled,
        )
    except TraceFeedbackNotFoundError as exc:
        raise http_error(404, "feedback_trace_not_found", "Trace not found") from exc
    except TraceFeedbackUnavailableError as exc:
        raise http_error(503, "trace_feedback_unavailable", "Trace feedback is unavailable") from exc
    except RuntimeError as exc:
        # The lifecycle owner rejects operations after startup failure or
        # shutdown. Keep that state a closed, sanitized transport error.
        raise http_error(503, "trace_feedback_unavailable", "Trace feedback is unavailable") from exc

    return TraceFeedbackResponse(
        trace_id=result.trace_id,
        value=result.value,
        assessment_id=result.assessment_id,
    )
