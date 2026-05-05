"""Trace feedback endpoints backed by MLflow, mirrored to Neon."""

from __future__ import annotations

from fastapi import APIRouter

from ..dependencies import HTTPIdentityDep, PersistedIdentityDep, PersistenceDep
from ..runtime_services.trace_service import TraceService
from ..schemas.feedback import TraceFeedbackRequest, TraceFeedbackResponse

router = APIRouter(prefix="/traces", tags=["traces"])


@router.post(
    "/feedback",
    response_model=TraceFeedbackResponse,
    responses={
        400: {
            "description": "The feedback request did not include a valid trace identifier."
        },
        401: {
            "description": "Authentication is required or the provided token is invalid."
        },
        403: {
            "description": "The authenticated user is not allowed to annotate this trace."
        },
        404: {"description": "No MLflow trace matched the provided identifier."},
        503: {
            "description": "MLflow feedback services are unavailable or misconfigured."
        },
    },
)
async def create_trace_feedback(
    request: TraceFeedbackRequest,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persisted_identity: PersistedIdentityDep,
) -> TraceFeedbackResponse:
    """Record human feedback and optional ground truth for an MLflow trace."""
    return await TraceService(persistence).create_trace_feedback(
        request=request,
        identity=identity,
        persisted_identity=persisted_identity,
    )
