"""Session experiment control without importing or running GEPA in the API process."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException

from fleet_rlm.api.deps import Experiments
from fleet_rlm.api.schemas import (
    ApiError,
    ExperimentDetail,
    ExperimentEnvelope,
    ExperimentList,
    ReviewExperiment,
    StartExperiment,
)
from fleet_rlm.experiments import ExperimentBusyError
from fleet_rlm.optimization_runs import ExperimentOptions, public_run

__all__ = ["Experiments", "router"]

router = APIRouter(prefix="/sessions/{session_id}/experiments", tags=["experiments"])
_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ApiError, "description": "The requested experiment does not exist."},
    409: {"model": ApiError, "description": "The operation conflicts with the run or current manager state."},
    422: {"model": ApiError, "description": "The request or experiment identity is invalid."},
    503: {"model": ApiError, "description": "Experiment storage or worker service is unavailable."},
}


def _error(error: Exception) -> HTTPException:
    """Map missing runs, conflicts, and invalid input to HTTP 404, 409, and 422.

    Return a generic 503 for other failures without exposing exception details.
    """
    if isinstance(error, FileNotFoundError):
        return HTTPException(status_code=404, detail="Experiment was not found")
    if isinstance(error, ExperimentBusyError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail="Invalid experiment identity or evidence")
    return HTTPException(status_code=503, detail="Experiment operation failed; check server configuration")


@contextmanager
def _experiment_errors() -> Generator[None, None, None]:
    try:
        yield
    except Exception as error:
        raise _error(error) from None


@router.get("", response_model=ExperimentList, response_model_exclude_unset=True, responses=_ERROR_RESPONSES)
async def list_experiments(session_id: str, manager: Experiments) -> dict[str, Any]:
    """List persisted history and the active run for one Session."""
    with _experiment_errors():
        return await manager.list(session_id)


@router.post(
    "",
    status_code=202,
    response_model=ExperimentEnvelope,
    response_model_exclude_unset=True,
    responses=_ERROR_RESPONSES,
)
async def start_experiment(session_id: str, body: StartExperiment, manager: Experiments) -> dict[str, Any]:
    """Start once per request ID; a busy server rejects instead of queueing work."""
    with _experiment_errors():
        options = ExperimentOptions.model_validate(body.model_dump(exclude={"request_id"}, exclude_unset=True))
        return {"experiment": public_run(await manager.start(session_id, body.request_id, options))}


@router.get("/{run_id}", response_model=ExperimentDetail, response_model_exclude_unset=True, responses=_ERROR_RESPONSES)
async def experiment_detail(session_id: str, run_id: str, manager: Experiments) -> dict[str, Any]:
    """Return a snapshot and its bounded saved comparison."""
    with _experiment_errors():
        return await manager.detail(session_id, run_id)


@router.post(
    "/{run_id}/cancel",
    status_code=202,
    response_model=ExperimentEnvelope,
    response_model_exclude_unset=True,
    responses=_ERROR_RESPONSES,
)
async def cancel_experiment(session_id: str, run_id: str, manager: Experiments) -> dict[str, Any]:
    """Request a cooperative stop; terminal status follows actual worker cleanup."""
    with _experiment_errors():
        return {"experiment": public_run(await manager.cancel(session_id, run_id))}


@router.post(
    "/{run_id}/review",
    status_code=202,
    response_model=ExperimentEnvelope,
    response_model_exclude_unset=True,
    responses=_ERROR_RESPONSES,
)
async def review_experiment(
    session_id: str, run_id: str, body: ReviewExperiment, manager: Experiments
) -> dict[str, Any]:
    """Approve through the evidence-checking worker, or persist an explicit rejection."""
    with _experiment_errors():
        return {"experiment": public_run(await manager.review(session_id, run_id, body.decision, body.request_id))}
