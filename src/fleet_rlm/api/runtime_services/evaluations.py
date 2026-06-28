"""Runtime services for evaluation endpoints.

This module provides the business logic for POST /api/v1/evaluations and
GET /api/v1/evaluations/{run_id}. The POST endpoint returns immediately with
a ``run_id`` and ``status="pending"``; the actual evaluation runs as a
background ``asyncio.create_task`` so the event loop is never blocked
(VAL-SEC-009, VAL-SEC-010, VAL-SEC-011). Results are held in a bounded
LRU in-memory store (VAL-SEC-012, VAL-SEC-013).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

# Lifecycle status constants for background evaluation runs.
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# Maximum number of evaluation runs kept in memory. When a 101st entry is
# added, the least-recently-used entry is evicted (VAL-SEC-012).
_EVALUATION_STORE_MAX_ENTRIES = 100


@dataclass
class _RunState:
    """Mutable lifecycle state for a background evaluation run.

    Attributes:
        run_id: Unique identifier for this evaluation run.
        status: Current lifecycle status (pending/running/completed/failed).
        report: The completed report (only set when ``status="completed"``).
        error: Error message (only set when ``status="failed"``).
        created_at: ISO8601 timestamp copied from the report once available.
        filters: Filters echoed from the request (available from creation).
    """

    run_id: str
    status: str = STATUS_PENDING
    report: EvaluationReport | None = None
    error: str | None = None
    created_at: str = ""
    filters: dict[str, Any] = field(default_factory=dict)


# Bounded LRU store for evaluation run state (run_id -> _RunState).
#
# An ``OrderedDict`` tracks access order: inserts and reads call
# ``move_to_end(run_id)`` to mark the entry most-recently-used, and when the
# store exceeds ``_EVALUATION_STORE_MAX_ENTRIES`` the oldest entry is evicted
# via ``popitem(last=False)`` (VAL-SEC-012, VAL-SEC-013).
_EVALUATION_STORE: "OrderedDict[str, _RunState]" = OrderedDict()

# Strong references to in-flight background tasks so the event loop does not
# garbage-collect them before they complete (RUF006). Entries are discarded
# by each task's ``done`` callback once the task finishes.
_INFLIGHT_TASKS: set[asyncio.Task[None]] = set()


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


def _store_insert(run_id: str, state: _RunState) -> None:
    """Insert or update a run state, enforcing the LRU bound.

    On insert/update the entry is moved to the most-recently-used position.
    If the store exceeds the configured maximum, the least-recently-used
    entry is evicted (VAL-SEC-012, VAL-SEC-013).
    """
    _EVALUATION_STORE[run_id] = state
    _EVALUATION_STORE.move_to_end(run_id)
    while len(_EVALUATION_STORE) > _EVALUATION_STORE_MAX_ENTRIES:
        _EVALUATION_STORE.popitem(last=False)


def _store_touch(run_id: str) -> _RunState | None:
    """Mark a run state as most-recently-used and return it (LRU access order).

    Returns ``None`` if the run_id is not in the store. Accessing an entry
    moves it to the most-recently-used position so it survives a subsequent
    eviction (VAL-SEC-013).
    """
    state = _EVALUATION_STORE.get(run_id)
    if state is None:
        return None
    _EVALUATION_STORE.move_to_end(run_id)
    return state


def _report_to_response(report: EvaluationReport) -> EvaluationReportResponse:
    """Convert a completed EvaluationReport to an API response."""
    return EvaluationReportResponse(
        run_id=report.run_id,
        status=STATUS_COMPLETED,
        created_at=report.created_at,
        filters=report.filters,
        per_trace=report.per_trace,
        aggregates=report.aggregates,
    )


def _state_to_response(state: _RunState) -> EvaluationReportResponse:
    """Convert a _RunState to an API response, including lifecycle status.

    For in-progress runs (pending/running) the report-specific fields are
    populated with empty/placeholder values so the client can poll again
    without blocking (VAL-SEC-010).
    """
    if state.report is not None:
        return _report_to_response(state.report)
    return EvaluationReportResponse(
        run_id=state.run_id,
        status=state.status,
        created_at=state.created_at,
        filters=state.filters,
        per_trace=[],
        aggregates={},
    )


async def _run_evaluation_task(
    run_id: str,
    request: EvaluationRequest,
) -> None:
    """Background task that runs the evaluation and records the result.

    The sync ``run_evaluation`` call is offloaded to a worker thread via
    ``asyncio.to_thread`` so the event loop is never blocked while the
    evaluation is in progress (VAL-SEC-011). On success the report is stored
    with ``status="completed"``; on failure the error is recorded with
    ``status="failed"``.
    """
    state = _store_touch(run_id)
    if state is None:
        # Evicted before the task started; nothing to update.
        logger.warning("Evaluation run %s evicted before background task started", run_id)
        return

    state.status = STATUS_RUNNING
    try:
        report = await asyncio.to_thread(
            run_evaluation,
            trace_ids=request.trace_ids,
            limit=request.limit,
            from_last_days=request.from_last_days,
        )
    except RuntimeError as e:
        # MLflow unreachable or other runtime error (VAL-C-058).
        logger.error("Evaluation run %s failed: %s", run_id, e)
        state.status = STATUS_FAILED
        state.error = str(e)
        return
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("Unexpected error during evaluation run %s", run_id)
        state.status = STATUS_FAILED
        state.error = f"Evaluation failed: {e}"
        return

    # Update the state in place (the entry is already MRU from _store_touch).
    # Normalize the report's run_id to the run_id the client received from the
    # POST response so polling always resolves to the same identifier (the
    # underlying run_evaluation may generate its own internal uuid).
    report.run_id = run_id
    state.status = STATUS_COMPLETED
    state.report = report
    state.created_at = report.created_at
    logger.info("Evaluation run completed: %s", run_id)


async def start_evaluation_run(request: EvaluationRequest) -> EvaluationRunResponse:
    """Start an evaluation run and return the run_id immediately.

    The evaluation runs as a background ``asyncio.create_task``; this function
    returns as soon as the run is registered with ``status="pending"``
    (VAL-SEC-009, VAL-SEC-011). Clients poll
    ``GET /api/v1/evaluations/{run_id}`` to observe the status transition and
    retrieve the completed report (VAL-SEC-010).

    Args:
        request: The evaluation request with filters (trace_ids, limit, from_last_days).

    Returns:
        Response containing the run_id and ``status="pending"``.
    """
    run_id = str(uuid.uuid4())
    state = _RunState(
        run_id=run_id,
        status=STATUS_PENDING,
        filters={
            "trace_ids": request.trace_ids,
            "limit": request.limit,
            "from_last_days": request.from_last_days,
        },
    )
    _store_insert(run_id, state)

    # Schedule the background evaluation task without blocking the response.
    # The task offloads the sync run_evaluation to a worker thread so the
    # event loop stays free to serve other requests (VAL-SEC-011).
    task = asyncio.create_task(_run_evaluation_task(run_id, request))
    _INFLIGHT_TASKS.add(task)
    task.add_done_callback(_INFLIGHT_TASKS.discard)

    logger.info("Evaluation run started (background): %s", run_id)
    return EvaluationRunResponse(run_id=run_id, status=STATUS_PENDING)


async def get_evaluation_report(run_id: str) -> EvaluationReportResponse:
    """Retrieve an evaluation report by run_id.

    For in-progress runs (``pending``/``running``) this returns the current
    status without blocking. For ``completed`` runs it returns the full
    report. For ``failed`` runs it raises 503. If the run is not in memory it
    falls back to disk (with a path containment check, VAL-SEC-005).

    Args:
        run_id: The unique identifier for the evaluation run.

    Returns:
        The evaluation report (or a status-only response while in progress).

    Raises:
        HTTPException: 404 if the run_id is not found; 503 if the run failed.
    """
    # Check in-memory store first (LRU access-order update, VAL-SEC-013).
    state = _store_touch(run_id)
    if state is not None:
        if state.status == STATUS_FAILED:
            raise HTTPException(
                status_code=503,
                detail=state.error or "Evaluation run failed",
            )
        return _state_to_response(state)

    # Fallback: try to load from disk (with path containment check, VAL-SEC-005).
    report_path = _resolve_report_path(run_id)
    if report_path.exists():
        try:
            report = EvaluationReport.read_from_disk(report_path.parent)
        except Exception as e:
            logger.warning("Failed to load report from disk: %s", e)
            raise HTTPException(
                status_code=404,
                detail=f"Evaluation run not found: {run_id}",
            ) from e
        # Cache it for future lookups (as a completed run).
        disk_state = _RunState(
            run_id=run_id,
            status=STATUS_COMPLETED,
            report=report,
            created_at=report.created_at,
        )
        _store_insert(run_id, disk_state)
        return _report_to_response(report)

    # Not found (VAL-C-018).
    raise HTTPException(
        status_code=404,
        detail=f"Evaluation run not found: {run_id}",
    )


async def list_evaluation_runs() -> EvaluationRunListResponse:
    """List all available evaluation runs with metadata.

    Returns:
        Response with list of evaluation runs, most recent first.
    """
    # Combine in-memory and disk-based reports.
    runs: list[EvaluationRunListItem] = []
    seen: set[str] = set()

    # First, add in-memory reports with their metadata. Only completed runs
    # have a populated created_at timestamp; in-progress runs are listed with
    # an empty timestamp so clients can still observe them.
    for run_id, state in _EVALUATION_STORE.items():
        seen.add(run_id)
        trace_count = len(state.report.per_trace) if state.report is not None else 0
        runs.append(
            EvaluationRunListItem(
                run_id=run_id,
                created_at=state.created_at or state.run_id,
                trace_count=trace_count,
            )
        )

    # Scan mlartifacts/eval/ directory for additional reports.
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
            # Skip if already in memory.
            if run_id in seen:
                continue
            # Load metadata from disk.
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

    # Sort by created_at descending (most recent first). In-progress runs with
    # an empty timestamp sort to the end via the run_id fallback.
    runs.sort(key=lambda r: r.created_at, reverse=True)

    return EvaluationRunListResponse(runs=runs)
