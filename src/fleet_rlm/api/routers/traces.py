"""Trace feedback endpoints backed by MLflow."""

from __future__ import annotations

from functools import partial
from typing import Any

from fastapi import APIRouter, HTTPException

from fleet_rlm.integrations.observability import log_trace_feedback, resolve_trace
from fleet_rlm.integrations.observability.config import MlflowConfig

from ..dependencies import HTTPIdentityDep
from ..runtime_services.common import RUNTIME_TEST_TIMEOUT_SECONDS, run_blocking
from ..schemas.core import TraceFeedbackRequest, TraceFeedbackResponse

router = APIRouter(prefix="/traces", tags=["traces"])


def _trace_info_payload(trace: object) -> dict[str, Any]:
    to_dict = getattr(trace, "to_dict", None)
    if callable(to_dict):
        try:
            payload = to_dict()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            info = payload.get("info")
            if isinstance(info, dict):
                return info

    info = getattr(trace, "info", None)
    if isinstance(info, dict):
        return dict(info)
    if info is None:
        return {}

    result: dict[str, Any] = {
        "trace_id": getattr(info, "trace_id", None),
        "client_request_id": getattr(info, "client_request_id", None),
    }
    trace_metadata = getattr(info, "trace_metadata", None)
    if isinstance(trace_metadata, dict):
        result["trace_metadata"] = trace_metadata
    return result


def _assert_feedback_access(
    trace_info: dict[str, Any],
    *,
    identity: Any,
) -> None:
    trace_metadata = trace_info.get("trace_metadata")
    if not isinstance(trace_metadata, dict):
        trace_metadata = {}

    trace_user = str(trace_metadata.get("mlflow.trace.user") or "").strip()
    if not trace_user or trace_user != identity.user_claim:
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to submit feedback for this MLflow trace.",
        )

    trace_workspace = str(trace_metadata.get("fleet_rlm.workspace_id") or "").strip()
    if trace_workspace and trace_workspace != identity.tenant_claim:
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to submit feedback for this MLflow trace.",
        )


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
) -> TraceFeedbackResponse:
    """Record human feedback and optional ground truth for an MLflow trace."""
    config = MlflowConfig.from_env()
    if not config.enabled:
        raise HTTPException(
            status_code=503,
            detail="MLflow feedback is unavailable because MLFLOW_ENABLED=false.",
        )

    try:
        trace = await run_blocking(
            partial(
                resolve_trace,
                trace_id=request.trace_id,
                client_request_id=request.client_request_id,
                config=config,
            ),
            timeout=RUNTIME_TEST_TIMEOUT_SECONDS,
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

    trace_info = _trace_info_payload(trace)
    _assert_feedback_access(trace_info, identity=identity)

    resolved_trace_id = str(trace_info.get("trace_id") or "")
    raw_client_request_id = trace_info.get("client_request_id")
    resolved_client_request_id = (
        str(raw_client_request_id).strip()
        if raw_client_request_id is not None
        else None
    ) or None

    if not resolved_trace_id:
        raise HTTPException(
            status_code=503,
            detail="Resolved MLflow trace is missing a trace id.",
        )

    try:
        outcome = await run_blocking(
            partial(
                log_trace_feedback,
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
            ),
            timeout=None,
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
