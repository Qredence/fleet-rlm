"""Global improvement loop: readiness, labelling queue, jobs, and history.

The API owns job processes but never imports GEPA; examples, programs, and jobs belong to no Session.
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from fleet_rlm.api.deps import Experiments, Settings, StoreDep, translate
from fleet_rlm.api.schemas import (
    ApiError,
    ImproveHistory,
    ImproveQueue,
    ImproveReadiness,
    JobDetail,
    JobEnvelope,
    JobList,
    LabelRequest,
    LabelSaved,
    QueueItem,
    ReviewJob,
    StartJob,
)
from fleet_rlm.contracts import figures
from fleet_rlm.experiments import ExperimentBusyError
from fleet_rlm.optimization_runs import ExperimentOptions, public_run

__all__ = ["router"]

router = APIRouter(prefix="/improve", tags=["improve"])
_PREVIEW_CHARS = 500
_MAX_FIGURES = 20
_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ApiError, "description": "The requested job does not exist."},
    409: {"model": ApiError, "description": "The operation conflicts with the job or current manager state."},
    422: {"model": ApiError, "description": "The request or job identity is invalid."},
    503: {"model": ApiError, "description": "Improve storage or worker service is unavailable."},
}


def _error(error: Exception) -> HTTPException:
    """Map missing jobs, conflicts, and invalid input to HTTP 404, 409, and 422.

    Return a generic 503 for other failures without exposing exception details.
    """
    if isinstance(error, FileNotFoundError):
        return HTTPException(status_code=404, detail="Job was not found")
    if isinstance(error, ExperimentBusyError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail="Invalid job request, identity, or evidence")
    return HTTPException(status_code=503, detail="Job operation failed; check server configuration")


@contextmanager
def _job_errors() -> Generator[None, None, None]:
    try:
        yield
    except Exception as error:
        raise _error(error) from None


def _preview(text: Any) -> str:
    return " ".join(str(text or "").split())[:_PREVIEW_CHARS]


@router.get("/readiness", response_model=ImproveReadiness, responses=_ERROR_RESPONSES)
def improve_readiness(store: StoreDep, settings: Settings) -> dict[str, Any]:
    """Report what the labelled examples support, what is missing, and how many labels are new."""
    from fleet_rlm.engine.compiled import CompiledProgramFile, installed_program_error
    from fleet_rlm.optimize.dataset import load_examples, readiness

    with translate("Readiness is unavailable"):
        target = settings.mlflow_target()
        report = readiness(load_examples(store, target.experiment_name, target.eval_experiment_name)).as_dict()
        # An installed program that contradicts the ledger would make every Turn refuse, so a job
        # would start from the wrong incumbent.
        path = Path(settings.compiled_program_dir) / "fleet.json"
        digest = hashlib.sha256(CompiledProgramFile(str(path)).read()).hexdigest() if path.exists() else ""
        if problem := installed_program_error(digest, store.approved_program()):
            report["missing"].append(problem)
            report["ready"] = False
        # Only a run that finished consumed the labels that existed when it started.
        last_run = max(
            (
                job["created_at"]
                for job in store.list_optimization_runs()
                if job["kind"] == "optimize" and job["status"] in {"completed", "approved"}
            ),
            default=None,
        )
        return {**report, "new_labels_since_last_run": store.new_review_labels(last_run)}


@router.get("/queue", response_model=ImproveQueue, responses=_ERROR_RESPONSES)
def improve_queue(store: StoreDep, limit: int = 50) -> ImproveQueue:
    """List replay-captured Turns from every Session with their label state and answer figures."""
    with translate("Review queue is unavailable"):
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        return ImproveQueue(
            items=[
                QueueItem(
                    turn_id=row["turn_id"],
                    session_id=row["session_id"],
                    task=_preview(row["task"]),
                    answer=_preview(row["answer"]),
                    figures=list(figures(row["answer"] or ""))[:_MAX_FIGURES],
                    label=row["review_status"] or "unlabelled",
                    expected_answer=row["expected_answer"],
                    expected_values=row["expected_values"] or [],
                    critical=bool(row["critical"]),
                    group=row["source_group"],
                    updated_at=row["updated_at"].isoformat(),
                )
                for row in store.improve_queue(limit)
            ]
        )


@router.post("/labels", response_model=LabelSaved, responses=_ERROR_RESPONSES)
def save_label(body: LabelRequest, store: StoreDep) -> LabelSaved:
    """Label one Turn; the server resolves its trace, so clients never handle trace or Session IDs."""
    from fleet_rlm.optimize.evaluation import EvaluationError, label_turn

    with translate("Label could not be saved"):
        reference = store.turn_reference(body.turn_id)
        if reference is None:
            raise HTTPException(status_code=404, detail="Turn has no captured replay")
        if not reference["trace_id"]:
            raise ValueError("Turn has no trace to label")
        try:
            labels = label_turn(
                store,
                reference["trace_id"],
                status=body.status,
                group=body.group,
                answer=body.expected_answer,
                values=body.expected_values,
                critical=body.critical,
            )
        except EvaluationError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None
        return LabelSaved(
            turn_id=body.turn_id,
            trace_id=reference["trace_id"],
            status=labels["review_status"],
            expected_answer=labels["expected_answer"],
            expected_values=labels["expected_values"],
            critical=labels["critical"],
            group=labels["source_group"],
        )


@router.get("/jobs", response_model=JobList, response_model_exclude_unset=True, responses=_ERROR_RESPONSES)
async def list_jobs(manager: Experiments) -> dict[str, Any]:
    """List persisted history and the active job."""
    with _job_errors():
        return await manager.list()


@router.post(
    "/jobs",
    status_code=202,
    response_model=JobEnvelope,
    response_model_exclude_unset=True,
    responses=_ERROR_RESPONSES,
)
async def start_job(body: StartJob, manager: Experiments) -> dict[str, Any]:
    """Start once per request ID; a busy server rejects instead of queueing work."""
    with _job_errors():
        options = ExperimentOptions.model_validate(body.model_dump(exclude={"request_id", "kind"}, exclude_unset=True))
        return {"job": public_run(await manager.start(body.request_id, options, body.kind))}


@router.get("/jobs/{run_id}", response_model=JobDetail, response_model_exclude_unset=True, responses=_ERROR_RESPONSES)
async def job_detail(run_id: str, manager: Experiments) -> dict[str, Any]:
    """Return a snapshot and its bounded saved comparison."""
    with _job_errors():
        return await manager.detail(run_id)


@router.post(
    "/jobs/{run_id}/cancel",
    status_code=202,
    response_model=JobEnvelope,
    response_model_exclude_unset=True,
    responses=_ERROR_RESPONSES,
)
async def cancel_job(run_id: str, manager: Experiments) -> dict[str, Any]:
    """Request a cooperative stop; terminal status follows actual worker cleanup."""
    with _job_errors():
        return {"job": public_run(await manager.cancel(run_id))}


@router.post(
    "/jobs/{run_id}/review",
    status_code=202,
    response_model=JobEnvelope,
    response_model_exclude_unset=True,
    responses=_ERROR_RESPONSES,
)
async def review_job(run_id: str, body: ReviewJob, manager: Experiments) -> dict[str, Any]:
    """Approve through the evidence-checking worker, or persist an explicit rejection."""
    with _job_errors():
        return {"job": public_run(await manager.review(run_id, body.decision, body.request_id))}


@router.get("/history", response_model=ImproveHistory, response_model_exclude_unset=True, responses=_ERROR_RESPONSES)
def improve_history(store: StoreDep) -> dict[str, Any]:
    """List approved versions and completed evaluation jobs, newest first, for the quality trend."""
    with translate("Improve history is unavailable"):
        jobs = store.list_optimization_runs()
        return {
            "versions": [
                public_run(job)
                for job in jobs
                if job["kind"] == "optimize" and job["status"] == "approved" and not job.get("legacy_session_id")
            ],
            "evaluations": [
                public_run(job) for job in jobs if job["kind"] != "optimize" and job["status"] == "completed"
            ],
        }
