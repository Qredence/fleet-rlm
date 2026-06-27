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
    EvaluationRunListItem,
    EvaluationRunListResponse,
    EvaluationRunResponse,
)

logger = logging.getLogger(__name__)

# In-memory store for evaluation reports (run_id -> report)
# In production, this would be backed by persistent storage
_EVALUATION_STORE: dict[str, EvaluationReport] = {}


def _eval_root() -> Path:
    """Return the resolved canonical evaluation artifacts root (VAL-SEC-005)."""
    return (Path.cwd() / "mlartifacts" / "eval").resolve()


def _resolve_report_path(run_id: str) -> Path:
    """Resolve and validate the report path for a run_id (VAL-SEC-005).

    The router-level UUID pattern check (VAL-SEC-004) already rejects most
    traversal attempts, but this defense-in-depth check ensures the resolved
    path stays within ``mlartifacts/eval/`` even if a symlink or OS-level path
    quirk would otherwise escape it.
    """
    eval_root = _eval_root()
    report_path = (eval_root / run_id / "report.json").resolve()
    # Containment check: the resolved report path must be inside the eval root.
    try:
        report_path.relative_to(eval_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Invalid run_id: {run_id}",
        ) from exc
    return report_path


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

    # Fallback: try to load from disk (with path containment check, VAL-SEC-005)
    report_path = _resolve_report_path(run_id)
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


async def list_evaluation_runs() -> EvaluationRunListResponse:
    """List all available evaluation runs with metadata.

    Returns:
        Response with list of evaluation runs, most recent first.
    """
    # Combine in-memory and disk-based reports
    runs: list[EvaluationRunListItem] = []

    # First, add in-memory reports with their metadata
    for run_id, report in _EVALUATION_STORE.items():
        runs.append(
            EvaluationRunListItem(
                run_id=run_id,
                created_at=report.created_at,
                trace_count=len(report.per_trace),
            )
        )

    # Scan mlartifacts/eval/ directory for additional reports
    eval_dir = _eval_root()
    if eval_dir.exists():
        for run_dir in eval_dir.iterdir():
            if not run_dir.is_dir():
                continue
            # Containment check: skip entries that resolve outside the eval root
            # (defense in depth against symlinks, VAL-SEC-005).
            try:
                run_dir.resolve().relative_to(eval_dir)
            except ValueError:
                logger.warning("Skipping directory outside eval root: %s", run_dir)
                continue
            if not (run_dir / "report.json").exists():
                continue
            run_id = run_dir.name
            # Skip if already in memory
            if run_id in _EVALUATION_STORE:
                continue
            # Load metadata from disk
            try:
                report = EvaluationReport.read_from_disk(run_dir)
                runs.append(
                    EvaluationRunListItem(
                        run_id=run_id,
                        created_at=report.created_at,
                        trace_count=len(report.per_trace),
                    )
                )
            except Exception as e:
                logger.warning("Failed to load report metadata for %s: %s", run_id, e)

    # Sort by created_at descending (most recent first)
    runs.sort(key=lambda r: r.created_at, reverse=True)

    return EvaluationRunListResponse(runs=runs)
