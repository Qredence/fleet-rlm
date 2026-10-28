"""Session experiment control without importing or running GEPA in the API process."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from fleet_rlm.api.schemas import _Request
from fleet_rlm.experiments import ExperimentBusyError, ExperimentManager
from fleet_rlm.optimization_runs import ExperimentOptions, public_run

router = APIRouter(prefix="/sessions/{session_id}/experiments", tags=["experiments"])


class StartExperiment(ExperimentOptions):
    """Validated search controls and a retry-safe caller identity."""

    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class ReviewExperiment(_Request):
    """An explicit human decision, never an implicit promotion after optimization."""

    decision: Literal["approved", "rejected"]
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


def get_experiments(request: Request) -> ExperimentManager:
    """Resolve only the lifespan resource; history access never obtains a Session runtime."""
    manager = request.app.state.experiments
    if manager is None:
        raise HTTPException(status_code=503, detail="Experiment storage is unavailable")
    return manager


Experiments = Annotated[ExperimentManager, Depends(get_experiments)]


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


@router.get("")
async def list_experiments(session_id: str, manager: Experiments) -> dict[str, Any]:
    """List persisted history and the active run for one Session."""
    try:
        return await manager.list(session_id)
    except Exception as error:
        raise _error(error) from None


@router.post("", status_code=202)
async def start_experiment(session_id: str, body: StartExperiment, manager: Experiments) -> dict[str, Any]:
    """Start once per request ID; a busy server rejects instead of queueing work."""
    try:
        options = ExperimentOptions.model_validate(body.model_dump(exclude={"request_id"}, exclude_unset=True))
        return {"experiment": public_run(await manager.start(session_id, body.request_id, options))}
    except Exception as error:
        raise _error(error) from None


@router.get("/{run_id}")
async def experiment_detail(session_id: str, run_id: str, manager: Experiments) -> dict[str, Any]:
    """Return a snapshot and its bounded saved comparison."""
    try:
        return await manager.detail(session_id, run_id)
    except Exception as error:
        raise _error(error) from None


@router.post("/{run_id}/cancel", status_code=202)
async def cancel_experiment(session_id: str, run_id: str, manager: Experiments) -> dict[str, Any]:
    """Request a cooperative stop; terminal status follows actual worker cleanup."""
    try:
        return {"experiment": public_run(await manager.cancel(session_id, run_id))}
    except Exception as error:
        raise _error(error) from None


@router.post("/{run_id}/review", status_code=202)
async def review_experiment(
    session_id: str, run_id: str, body: ReviewExperiment, manager: Experiments
) -> dict[str, Any]:
    """Approve through the evidence-checking worker, or persist an explicit rejection."""
    try:
        return {"experiment": public_run(await manager.review(session_id, run_id, body.decision, body.request_id))}
    except Exception as error:
        raise _error(error) from None
