"""Runtime services for evaluation endpoints.

This module provides the business logic for POST /api/v1/evaluations and
GET /api/v1/evaluations/{run_id}. It delegates to quality/eval.run_evaluation
following the thin-router pattern (VAL-C-048).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import HTTPException

from fleet_rlm.quality.eval import run_evaluation
from fleet_rlm.quality.eval.report import EvaluationReport

from ..schemas.evaluations import (
    EvaluationReportResponse,
    EvaluationRequest,
    EvaluationRunResponse,
)

logger = logging.getLogger(__name__)

# In-memory store for evaluation reports (run_id -> report)
# In production, this would be backed by persistent storage
_EVALUATION_STORE: dict[str, EvaluationReport] = {}


def _report_to_response(report: EvaluationReport) -> EvaluationReportResponse:
    """Convert an EvaluationReport to an API response."""
    return EvaluationReportResponse(
        run_id=report.run_id,
        created_at=report.created_at,
        filters=report.filters,
        per_trace=report.per_trace,
        aggregates=report.aggregates,
    )


async def start_evaluation_run(request: EvaluationRequest) -> EvaluationRunResponse:
    """Start an evaluation run and return the run_id.

    This function delegates to quality/eval.run_evaluation and stores the
    result in memory for later retrieval.

    Args:
        request: The evaluation request with filters (trace_ids, limit, from_last_days).

    Returns:
        Response containing the run_id.

    Raises:
        HTTPException: If MLflow is unreachable or evaluation fails.
    """
    try:
        # Delegate to quality/eval package (VAL-C-048 thin-router pattern)
        report = run_evaluation(
            trace_ids=request.trace_ids,
            limit=request.limit,
            from_last_days=request.from_last_days,
        )

        # Store the report for later retrieval
        _EVALUATION_STORE[report.run_id] = report

        logger.info("Evaluation run completed: %s", report.run_id)
        return EvaluationRunResponse(run_id=report.run_id)

    except RuntimeError as e:
        # MLflow unreachable or other runtime error (VAL-C-058)
        logger.error("Evaluation run failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error during evaluation")
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {e}") from e


async def get_evaluation_report(run_id: str) -> EvaluationReportResponse:
    """Retrieve an evaluation report by run_id.

    Args:
        run_id: The unique identifier for the evaluation run.

    Returns:
        The full evaluation report.

    Raises:
        HTTPException: If the run_id is not found (404).
    """
    # Check in-memory store first
    if run_id in _EVALUATION_STORE:
        report = _EVALUATION_STORE[run_id]
        return _report_to_response(report)

    # Fallback: try to load from disk
    report_path = Path.cwd() / "mlartifacts" / "eval" / run_id / "report.json"
    if report_path.exists():
        try:
            report = EvaluationReport.read_from_disk(report_path.parent)
            # Cache it for future lookups
            _EVALUATION_STORE[run_id] = report
            return _report_to_response(report)
        except Exception as e:
            logger.warning("Failed to load report from disk: %s", e)

    # Not found (VAL-C-018)
    raise HTTPException(
        status_code=404,
        detail=f"Evaluation run not found: {run_id}",
    )


def list_evaluation_runs() -> list[str]:
    """List all available evaluation run IDs.

    Returns:
        List of run_id strings.
    """
    # Combine in-memory and disk-based reports
    run_ids = set(_EVALUATION_STORE.keys())

    # Scan mlartifacts/eval/ directory for additional reports
    eval_dir = Path.cwd() / "mlartifacts" / "eval"
    if eval_dir.exists():
        for run_dir in eval_dir.iterdir():
            if run_dir.is_dir() and (run_dir / "report.json").exists():
                run_ids.add(run_dir.name)

    return sorted(run_ids)
