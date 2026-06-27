"""Evaluation endpoints for GenAI trace quality assessment.

This module provides POST /api/v1/evaluations to kick off an evaluation run
and GET /api/v1/evaluations/{run_id} to retrieve the full report JSON.

Following the thin-router pattern (VAL-C-048), route handlers delegate to
runtime_services/evaluations.py which in turn delegates to quality/eval.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

from ..dependencies import HTTPIdentityDep, PersistedIdentityDep, PersistenceDep
from ..runtime_services import evaluations as evaluation_service
from ..schemas.evaluations import (
    EvaluationReportResponse,
    EvaluationRequest,
    EvaluationRunListResponse,
    EvaluationRunResponse,
)

# UUID pattern for run_id path parameter (VAL-SEC-004).
# Rejects path traversal sequences (.., %2F, slashes) and non-UUID strings
# at the router layer before the service layer is reached.
_RUN_ID_PATTERN = r"^[a-f0-9-]{36}$"

router = APIRouter(
    prefix="/evaluations",
    tags=["evaluations"],
)


@router.post(
    "",
    response_model=EvaluationRunResponse,
    responses={
        401: {"description": "Authentication is required or the provided token is invalid."},
        503: {"description": "MLflow or evaluation services are unavailable."},
    },
    summary="Start evaluation run",
    description=(
        "Kick off a GenAI evaluation run on MLflow traces. Returns a run_id "
        "that can be used to retrieve the full report via GET /evaluations/{run_id}."
    ),
)
async def start_evaluation(
    request: EvaluationRequest,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persisted_identity: PersistedIdentityDep,
) -> EvaluationRunResponse:
    """Start an evaluation run and return the run_id (VAL-C-014, VAL-C-017, VAL-C-056)."""
    _ = (identity, persistence, persisted_identity)
    return await evaluation_service.start_evaluation_run(request)


@router.get(
    "",
    response_model=EvaluationRunListResponse,
    responses={
        401: {"description": "Authentication is required or the provided token is invalid."},
    },
    summary="List evaluation runs",
    description=(
        "Return a list of all evaluation runs, most recent first. "
        "Use the run_id to fetch the full report via GET /evaluations/{run_id}."
    ),
)
async def list_evaluations(
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persisted_identity: PersistedIdentityDep,
) -> EvaluationRunListResponse:
    """List all evaluation runs (VAL-C-050)."""
    _ = (identity, persistence, persisted_identity)
    return await evaluation_service.list_evaluation_runs()


@router.get(
    "/{run_id}",
    response_model=EvaluationReportResponse,
    responses={
        401: {"description": "Authentication is required or the provided token is invalid."},
        404: {"description": "Evaluation run not found."},
    },
    summary="Get evaluation report",
    description=(
        "Retrieve the full evaluation report for a given run_id. The report includes "
        "per-trace scores (4 judges + 6 metrics) and aggregate statistics."
    ),
)
async def get_evaluation(
    run_id: Annotated[
        str,
        Path(
            description="Unique identifier (UUID) for the evaluation run.",
            pattern=_RUN_ID_PATTERN,
        ),
    ],
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    persisted_identity: PersistedIdentityDep,
) -> EvaluationReportResponse:
    """Retrieve the full evaluation report (VAL-C-015, VAL-C-016, VAL-C-018)."""
    _ = (identity, persistence, persisted_identity)
    return await evaluation_service.get_evaluation_report(run_id)
