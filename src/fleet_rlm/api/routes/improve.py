"""Global improvement loop: readiness, labelling queue, jobs, and history.

The API owns job processes but never imports GEPA; examples, programs, and jobs belong to no Session.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException

from fleet_rlm.api.deps import Experiments, Settings, StoreDep, translate
from fleet_rlm.api.schemas import (
    ApiError,
    ImproveHistory,
    ImproveQueue,
    ImproveReadiness,
    ImproveSettings,
    ImproveSettingsUpdate,
    JobDetail,
    JobEnvelope,
    JobList,
    LabelRequest,
    LabelSaved,
    QueueItem,
    ReviewJob,
    StartJob,
)
from fleet_rlm.config import FleetSettings
from fleet_rlm.contracts import figures
from fleet_rlm.experiments import ExperimentBusyError, ExperimentManager
from fleet_rlm.optimization_runs import ExperimentOptions, public_run
from fleet_rlm.store import Store

__all__ = ["router"]

_LOGGER = logging.getLogger(__name__)
_SETTINGS_SCOPE = "improve"

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


def _readiness(store: Store, settings: FleetSettings) -> tuple[dict[str, Any], str | None]:
    """The readiness report and the start time of the last finished optimize job the label count follows."""
    from fleet_rlm.engine.compiled import installed_program_error
    from fleet_rlm.optimize.dataset import load_examples, readiness
    from fleet_rlm.optimize.jobs import installed_digest

    target = settings.mlflow_target()
    report = readiness(load_examples(store, target.experiment_name, target.eval_experiment_name)).as_dict()
    # An installed program that contradicts the ledger would make every Turn refuse, so a job
    # would start from the wrong incumbent.
    if problem := installed_program_error(installed_digest(settings), store.approved_program()):
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
    return {**report, "new_labels_since_last_run": store.new_review_labels(last_run)}, last_run


@router.get("/readiness", response_model=ImproveReadiness, responses=_ERROR_RESPONSES)
def improve_readiness(store: StoreDep, settings: Settings) -> dict[str, Any]:
    """Report what the labelled examples support, what is missing, and how many labels are new."""
    with translate("Readiness is unavailable"):
        return _readiness(store, settings)[0]


_NEEDS_TOKEN_CAP = "Auto-run needs a token cap: set max_total_tokens above 0 (0 is unlimited)."


def _effective_settings(saved: dict[str, Any], settings: FleetSettings) -> dict[str, Any]:
    """Environment defaults overridden by saved values, plus why auto-run cannot start (if it cannot)."""
    defaults = {
        "auto_run": settings.auto_optimize,
        "min_new_labels": settings.auto_optimize_min_new_labels,
        "max_metric_calls": settings.auto_optimize_max_metric_calls,
        "max_total_tokens": settings.job_max_tokens,
    }
    merged = {**defaults, **{name: value for name, value in saved.items() if name in defaults}}
    blocked = _NEEDS_TOKEN_CAP if merged["auto_run"] and merged["max_total_tokens"] == 0 else None
    return {**merged, "auto_run_blocked": blocked}


def _improve_settings(store: Store, settings: FleetSettings) -> dict[str, Any]:
    return _effective_settings(store.global_settings(_SETTINGS_SCOPE), settings)


@router.get("/settings", response_model=ImproveSettings, responses=_ERROR_RESPONSES)
def get_improve_settings(store: StoreDep, settings: Settings) -> dict[str, Any]:
    """Auto-run switch and budgets in force: saved values over the environment defaults."""
    with translate("Improve settings are unavailable"):
        return _improve_settings(store, settings)


@router.put("/settings", response_model=ImproveSettings, responses=_ERROR_RESPONSES)
def put_improve_settings(body: ImproveSettingsUpdate, store: StoreDep, settings: Settings) -> dict[str, Any]:
    """Save the supplied fields; an explicit null clears a saved value back to its environment default."""
    with translate("Improve settings could not be saved"):
        changes = body.model_dump(exclude_unset=True)
        saved = store.global_settings(_SETTINGS_SCOPE)
        proposed = {
            **{k: v for k, v in saved.items() if k not in changes},
            **{k: v for k, v in changes.items() if v is not None},
        }
        # Automatic jobs spend money unattended, so they are never allowed without a token cap.
        if _effective_settings(proposed, settings)["auto_run_blocked"]:
            raise ValueError(_NEEDS_TOKEN_CAP)
        store.update_global_settings(_SETTINGS_SCOPE, changes)
        return _improve_settings(store, settings)


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


def _save_label(body: LabelRequest, store: Store) -> LabelSaved:
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
                default_answer=reference.get("answer"),
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


async def _auto_run(store: Store, settings: FleetSettings, manager: ExperimentManager) -> None:
    """Start an optimize job when auto-run is on and ready, enough labels are new, and nothing is active.

    The request ID derives from the label state, so a repeated check cannot start a second job, and a
    failed job is retried only after another label arrives. The job only creates a candidate: it is
    never approved here. A label is already saved, so no failure may surface to the caller.
    """
    try:
        preferences = await asyncio.to_thread(_improve_settings, store, settings)
        if not preferences["auto_run"]:
            return
        if preferences["auto_run_blocked"]:
            _LOGGER.info("Automatic optimize skipped: %s", preferences["auto_run_blocked"])
            return
        report, last_run = await asyncio.to_thread(_readiness, store, settings)
        if not report["ready"] or report["new_labels_since_last_run"] < preferences["min_new_labels"]:
            return
        if (await manager.list())["active_run_id"] is not None:
            return
        stamp = hashlib.sha256(f"{last_run}:{report['new_labels_since_last_run']}".encode()).hexdigest()[:16]
        options = ExperimentOptions(
            **({"max_metric_calls": preferences["max_metric_calls"]} if preferences["max_metric_calls"] else {}),
            max_total_tokens=preferences["max_total_tokens"],
        )
        await manager.start(f"auto-{stamp}", options, "optimize", trigger="auto")
    except ExperimentBusyError:
        _LOGGER.debug("Automatic optimize skipped: another operation holds the job slot")
    except Exception:
        _LOGGER.warning("Automatic optimize check failed", exc_info=True)


@router.post("/labels", response_model=LabelSaved, responses=_ERROR_RESPONSES)
async def save_label(body: LabelRequest, store: StoreDep, settings: Settings, manager: Experiments) -> LabelSaved:
    """Label one Turn; the server resolves its trace, so clients never handle trace or Session IDs.

    A saved label may then start an automatic optimize job when the saved settings allow it.
    """
    saved = await asyncio.to_thread(_save_label, body, store)
    await _auto_run(store, settings, manager)
    return saved


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
async def start_job(body: StartJob, manager: Experiments, store: StoreDep, settings: Settings) -> dict[str, Any]:
    """Start once per request ID; a busy server rejects instead of queueing work."""
    with _job_errors():
        values = body.model_dump(exclude={"request_id", "kind"}, exclude_unset=True)
        # The saved token cap is the default for every job kind; a request's own cap still wins.
        values.setdefault(
            "max_total_tokens", (await asyncio.to_thread(_improve_settings, store, settings))["max_total_tokens"]
        )
        options = ExperimentOptions.model_validate(values)
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
