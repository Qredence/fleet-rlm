"""Trace service encapsulating MLflow trace feedback logic."""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from fastapi import HTTPException

from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.integrations.observability import log_trace_feedback, resolve_trace
from fleet_rlm.integrations.observability.config import MlflowConfig

from ..schemas.feedback import TraceFeedbackRequest, TraceFeedbackResponse
from .common import RUNTIME_TEST_TIMEOUT_SECONDS, run_blocking

logger = logging.getLogger(__name__)


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


class TraceService:
    """Encapsulates MLflow trace feedback resolution, access control, and persistence."""

    def __init__(self, persistence: Any) -> None:
        self._persistence = persistence

    async def create_trace_feedback(
        self,
        *,
        request: TraceFeedbackRequest,
        identity: Any,
        persisted_identity: IdentityUpsertResult,
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
            str(raw_client_request_id).strip() if raw_client_request_id is not None else None
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

        try:
            await self._persistence.store_trace_feedback(
                tenant_id=persisted_identity.tenant_id,
                workspace_id=persisted_identity.workspace_id,
                reviewer_user_id=persisted_identity.user_id,
                trace_id=resolved_trace_id,
                client_request_id=resolved_client_request_id,
                is_correct=request.is_correct,
                comment=request.comment,
                expected_response=request.expected_response,
                metadata_json={
                    "source": "mlflow",
                    "mlflow_outcome": {
                        "feedback_logged": bool(outcome.get("feedback_logged", False)),
                        "expectation_logged": bool(outcome.get("expectation_logged", False)),
                    },
                },
            )
        except Exception as exc:
            logger.warning(
                "trace_feedback_persist_failed",
                extra={"trace_id": resolved_trace_id},
                exc_info=exc,
            )
            raise HTTPException(
                status_code=503,
                detail="Failed to persist trace feedback.",
            ) from exc

        return TraceFeedbackResponse(
            trace_id=resolved_trace_id,
            client_request_id=resolved_client_request_id,
            feedback_logged=bool(outcome.get("feedback_logged", False)),
            expectation_logged=bool(outcome.get("expectation_logged", False)),
        )
