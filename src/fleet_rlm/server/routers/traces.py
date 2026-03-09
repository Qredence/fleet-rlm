"""Trace feedback endpoints backed by MLflow."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from fleet_rlm.analytics import MlflowConfig, log_trace_feedback, resolve_trace

from ..deps import HTTPIdentityDep
from ..schemas.core import TraceFeedbackRequest, TraceFeedbackResponse

router = APIRouter(prefix="/traces", tags=["traces"])


@router.post("/feedback", response_model=TraceFeedbackResponse)
async def create_trace_feedback(
    request: TraceFeedbackRequest,
    identity: HTTPIdentityDep,
) -> TraceFeedbackResponse:
    """Record human feedback and optional ground truth for an MLflow trace."""
    config = MlflowConfig.from_env()
    if not config.enabled:
        raise HTTPException(
            status_code=503,
            detail="MLflow feedback is unavailable because MLFLOW_ENABLED=false.",
        )

    try:
        trace = resolve_trace(
            trace_id=request.trace_id,
            client_request_id=request.client_request_id,
            config=config,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to resolve MLflow trace: {exc}",
        ) from exc

    if trace is None:
        raise HTTPException(
            status_code=404,
            detail="Unable to find an MLflow trace for the provided identifier.",
        )

    resolved_trace_id = str(getattr(getattr(trace, "info", None), "trace_id", "") or "")
    resolved_client_request_id = getattr(
        getattr(trace, "info", None),
        "client_request_id",
        None,
    )

    if not resolved_trace_id:
        raise HTTPException(
            status_code=503,
            detail="Resolved MLflow trace is missing a trace id.",
        )

    try:
        outcome = log_trace_feedback(
            trace_id=resolved_trace_id,
            is_correct=request.is_correct,
            source_id=identity.user_claim,
            comment=request.comment,
            expected_response=request.expected_response,
            metadata={
                "tenant_claim": identity.tenant_claim,
                "email": identity.email or "",
                "name": identity.name or "",
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to log MLflow feedback: {exc}",
        ) from exc

    return TraceFeedbackResponse(
        trace_id=resolved_trace_id,
        client_request_id=resolved_client_request_id,
        feedback_logged=bool(outcome.get("feedback_logged", False)),
        expectation_logged=bool(outcome.get("expectation_logged", False)),
    )
