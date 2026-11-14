"""Progress reporting, budgets, cooperative stop fences, and optional telemetry for optimization jobs."""

from __future__ import annotations

import argparse
import logging
import os
import re
import stat
import sys
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any

import mlflow

from fleet_rlm.config import FleetSettings
from fleet_rlm.observability import LMUsageCallback
from fleet_rlm.optimization_runs import emit_event
from fleet_rlm.optimize.validation import RolloutCleanup
from fleet_rlm.store import Store

logger = logging.getLogger(__name__)

_SCORE_TRAJECTORY_POINTS = 200
_CONTRACT_MODULES = (
    "fleet_rlm.optimize.gepa",
    "fleet_rlm.optimize.progress",
    "fleet_rlm.optimize.runner",
    "fleet_rlm.optimize.cli_commands",
)
# Messages are shown only for failures that describe a provider, endpoint, or Fleet's own contracts. Other
# exceptions can embed evaluated content (task text, answers, payloads), so they are named but not quoted.
_PATH = re.compile(r"(?<![\w.:\]-])/(?:[\w.~-]+/)+[\w.~-]*|[A-Za-z]:\\[^\s'\"]+")
_FAILURE_MESSAGE_CHARS = 300
_GENERIC_FAILURE = "Optimization failed; exception details omitted."


class OptimizationPersistenceError(RuntimeError):
    """Required optimization persistence or ownership could not be confirmed."""


class OptimizationCancelledError(Exception):
    """Raised after a cooperative GEPA or validation stop request is observed."""


class BudgetExhaustedError(OptimizationCancelledError):
    """Raised at a safe boundary once the job's total token cap is spent."""


class JudgeFailedError(RuntimeError):
    """The correctness judge raised, so scores computed from it are not evidence."""


def failure_reason(error: BaseException) -> str:
    """The error's class and, for provider or contract errors only, a short message with secrets and paths redacted."""
    from fleet_rlm.observability import activity_text

    name = type(error).__name__
    if name == "LMUnsupportedModelError":
        return "LMUnsupportedModelError: model is not supported on this endpoint"
    if not type(error).__module__.startswith(_CONTRACT_MODULES) or isinstance(error, JudgeFailedError):
        return name
    message = _PATH.sub("[path]", activity_text(" ".join(str(error).split()), limit=_FAILURE_MESSAGE_CHARS))
    return f"{name}: {message}" if message else name


def failure_detail(error: BaseException) -> str:
    """The job's stored failure detail: the safe reason when there is a message to show, else a generic line."""
    reason = failure_reason(error)
    return reason if ": " in reason else _GENERIC_FAILURE


class TokenBudget(LMUsageCallback):
    """Sum generative LM tokens across a whole job and report when its optional cap is reached.

    This is telemetry with a stop predicate. It never touches the reporter's persistence latch,
    and TypeSafe decision calls are excluded exactly as they are from Turn usage.
    """

    def __init__(self, cap: int) -> None:
        super().__init__()
        self.cap = cap
        self.used = 0
        self._count_lock = threading.Lock()

    def on_lm_end(self, call_id: str, outputs: Any, exception: BaseException | None = None) -> None:  # noqa: ARG002
        """Add the call's reported tokens, whether it succeeded or failed."""
        usage = self.finish_usage(call_id)
        tokens = usage.get("total_tokens", usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
        with self._count_lock:
            self.used += int(tokens)

    @property
    def reached(self) -> bool:
        """True once a nonzero cap has been spent."""
        return self.cap > 0 and self.used >= self.cap

    def should_stop(self, *_args: Any, **_kwargs: Any) -> bool:
        """GEPA's native stop predicate."""
        return self.reached


class GEPAProgressReporter:
    """Own durable worker observations and bridge callback failures to native stopping."""

    def __init__(
        self,
        run_id: str | None = None,
        *,
        store: Store | None = None,
        owner_id: str | None = None,
        emit_events: bool = True,
        tokens: TokenBudget | None = None,
    ) -> None:
        self.run_id = run_id
        self.store = store
        self.owner_id = owner_id
        self.tokens = tokens
        self.emit_events = emit_events
        self._lock = RLock()
        self._failure: Exception | None = None
        self._progress: dict[str, Any] = {}
        self._diagnostic_failed = False

    def should_stop(self, *_args: Any, **_kwargs: Any) -> bool:
        """Request GEPA's native safe-point stop after the first required failure."""
        with self._lock:
            try:
                self.raise_if_failed()
            except OptimizationPersistenceError:
                return True
            return False

    def raise_if_failed(self) -> None:
        """Reject partial native results even when GEPA swallowed a callback exception."""
        with self._lock:
            if isinstance(self.store, Store) and self._failure is None:
                try:
                    self.store.assert_optimization_lease()
                except Exception as error:
                    self._failure = error
            if self._failure is not None:
                message = (
                    "Optimization lease was lost or expired"
                    if str(self._failure) == "Optimization lease was lost or expired"
                    else "Required optimization persistence failed"
                )
                raise OptimizationPersistenceError(message) from self._failure

    def update(self, **updates: Any) -> None:
        """Persist a required owner-fenced write; retain its first failure for native stopping."""
        with self._lock:
            self.raise_if_failed()
            if self.store is None or self.run_id is None:
                return
            try:
                saved = _update_run(
                    self.store,
                    self.run_id,
                    owner_id=self.owner_id,
                    expected_statuses=("running",),
                    **updates,
                )
                if saved is None:
                    raise OptimizationPersistenceError("Optimization ownership or status changed")
            except Exception as error:
                self._failure = error
                self.raise_if_failed()

    def _persist(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            progress = {
                key: value
                for key, value in event.items()
                if key
                in {
                    "iteration",
                    "metric_calls_used",
                    "metric_calls_delta",
                    "metric_calls_remaining",
                    "best_score",
                    "score_trajectory",
                    "validation_done",
                    "validation_total",
                }
            }
            if "validation_done" in progress and progress.get("validation_total") == self._progress.get(
                "validation_total"
            ):
                progress["validation_done"] = max(progress["validation_done"], self._progress.get("validation_done", 0))
            # Only the cumulative total is monotonic. Delta and remaining are observations.
            if "metric_calls_used" in progress and progress["metric_calls_used"] < self._progress.get(
                "metric_calls_used", 0
            ):
                return
            if self.tokens is not None:
                progress |= {"tokens_used": self.tokens.used, "token_cap": self.tokens.cap}
            snapshot = self._progress | progress
            self.update(progress=snapshot, stage=event.get("stage", "search"))
            self._progress = snapshot

    def _observe(self, progress: Mapping[str, Any]) -> None:
        try:
            self._persist(progress)
        except OptimizationPersistenceError:
            # Native GEPA catches callback exceptions. The stop predicate and explicit
            # stage boundaries, rather than exception propagation, enforce durability.
            return
        if self.emit_events:
            try:
                _emit_diagnostic({"type": "progress", **progress, **({"run_id": self.run_id} if self.run_id else {})})
            except Exception:
                with self._lock:
                    if not self._diagnostic_failed:
                        logger.warning("Optimization diagnostic emission failed; sensitive details omitted.")
                        self._diagnostic_failed = True

    def on_budget_updated(self, event: Mapping[str, Any]) -> None:
        """Persist known counters without estimating unrelated or remaining work."""
        self._observe(
            {
                "stage": "search",
                **{
                    key: event[key]
                    for key in ("iteration", "metric_calls_used", "metric_calls_delta", "metric_calls_remaining")
                    if key in event
                },
            }
        )

    def on_valset_evaluated(self, event: Mapping[str, Any]) -> None:
        """Persist the best validation score and a bounded [iteration, score] trajectory."""
        score = float(event["average_score"])
        with self._lock:
            trajectory = [*self._progress.get("score_trajectory", ()), [event["iteration"], score]]
            if len(trajectory) > _SCORE_TRAJECTORY_POINTS:
                thinned = trajectory[::2]
                trajectory = thinned if thinned[-1] is trajectory[-1] else [*thinned, trajectory[-1]]
            update: dict[str, Any] = {"stage": "search", "score_trajectory": trajectory}
            if event["is_best_program"] or "best_score" not in self._progress:
                update["best_score"] = score
        self._observe(update)

    def on_validation_progress(self, done: int, total: int) -> None:
        """Persist how many paired-validation rollouts have finished; the worker owns this counter."""
        self._observe({"stage": "validation", "validation_done": done, "validation_total": total})

    def on_optimization_end(self, event: Mapping[str, Any]) -> None:
        """Retain prior counters while updating GEPA's final observed total."""
        self._observe({"stage": "search", "metric_calls_used": event["total_metric_calls"]})


class _JudgeLatch:
    """Remember the first judge failure so the job fails with a clear reason instead of scoring on a broken judge."""

    def __init__(self) -> None:
        self.reason: str | None = None
        self._lock = threading.Lock()

    def record(self, error: BaseException) -> None:
        with self._lock:
            if self.reason is None:
                self.reason = failure_reason(error)

    def should_stop(self, *_args: Any, **_kwargs: Any) -> bool:
        """GEPA's native stop predicate."""
        return self.reason is not None

    def raise_if_failed(self) -> None:
        if self.reason is not None:
            raise JudgeFailedError(f"The correctness judge failed ({self.reason})")


def _check_cancelled(stop_file: Path) -> None:
    """Honor a cooperative stop at safe stage boundaries."""
    if stop_file.exists():
        raise OptimizationCancelledError("Optimization stop requested")


def _database_stop_requested(store: Store | None, run_id: str | None) -> bool:
    """Read the durable stop fence between GEPA batches and evaluation stages."""
    if store is None or run_id is None:
        return False
    record = store.get_optimization_run(run_id)
    return record is None or record.get("status") not in {"running", "queued"} or bool(record.get("stop_requested"))


class _DatabaseStopper:
    """Expose the DB stop fence as GEPA's native callable stop callback."""

    def __init__(self, store: Store, run_id: str) -> None:
        self.store = store
        self.run_id = run_id

    def __call__(self, *_args: Any, **_kwargs: Any) -> bool:
        """Return whether the run is missing, inactive, or has a durable stop request."""
        return _database_stop_requested(self.store, self.run_id)


def build_proposal_stop_callback(max_proposals: int) -> Any:
    """Build the GEPA proposal stop hook used for explicit bounded searches."""
    from gepa.utils.stop_condition import MaxCandidateProposalsStopper

    return MaxCandidateProposalsStopper(max_proposals)


def build_stop_file_callback(stop_file: Path) -> Any:
    """Stop native GEPA at its next safe iteration boundary after a UI cancel request."""
    from gepa.utils.stop_condition import FileStopper

    return FileStopper(str(stop_file))


class OptimizationStopPolicy:
    """Unified job budgets, durable progress reporter, rollout cleanup, judge latch, and stop fences."""

    def __init__(
        self,
        args: argparse.Namespace,
        settings: FleetSettings,
        store: Store,
        run_id: str,
        owner_id: str | None,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.owner_id = owner_id
        self.stop_file = Path(settings.compiled_program_dir) / "runs" / run_id / "gepa.stop"
        self.stop_file.parent.mkdir(parents=True, exist_ok=True)
        cap = getattr(args, "max_total_tokens", None)
        self.tokens = TokenBudget(settings.job_max_tokens if cap is None else cap)
        self.reporter = GEPAProgressReporter(
            run_id, store=store, owner_id=owner_id, emit_events=False, tokens=self.tokens
        )
        self.cleanup = RolloutCleanup()
        self.judge = _JudgeLatch()
        # Each labelled Turn's latest evaluation score, by Turn ID, for the Improve views.
        self.example_scores: dict[str, float] = {}

    def stop_callbacks(self, *, max_candidate_proposals: int | None = None) -> list[Any]:
        """Return GEPA's ordered native stop predicates across file, DB, persistence, cleanup, tokens, and judge."""
        callbacks: list[Any] = [
            build_stop_file_callback(self.stop_file),
            _DatabaseStopper(self.store, self.run_id),
            self.reporter.should_stop,
            self.cleanup.should_stop,
            self.tokens.should_stop,
            self.judge.should_stop,
        ]
        if max_candidate_proposals is not None:
            callbacks.append(build_proposal_stop_callback(max_candidate_proposals))
        return callbacks

    def check_boundary(self) -> None:
        """Raise when persistence failed, a stop or ownership change was observed, or the token cap is spent."""
        self.reporter.raise_if_failed()
        _check_cancelled(self.stop_file)
        record = self.store.get_optimization_run(self.run_id)
        if record is None or (self.owner_id is not None and record.get("owner_id") != self.owner_id):
            raise OptimizationPersistenceError("Optimization ownership changed")
        if record.get("stop_requested"):
            raise OptimizationCancelledError("Optimization stop requested")
        if record.get("status") != "running":
            raise OptimizationPersistenceError("Optimization status changed")
        self.cleanup.raise_if_failed()
        self.judge.raise_if_failed()
        if self.tokens.reached:
            raise BudgetExhaustedError("Optimization token budget exhausted")

    def set_stage(self, name: str) -> None:
        """Check the boundary, then publish the stage."""
        self.check_boundary()
        self.reporter.update(stage=name)


def _emit_diagnostic(event: Mapping[str, Any], *, suppress_fifo: bool = True) -> None:
    """Avoid pipe backpressure in callback/control paths; API output is discarded."""
    try:
        if suppress_fifo and stat.S_ISFIFO(os.fstat(sys.stdout.fileno()).st_mode):
            return
    except (AttributeError, OSError, ValueError):
        # In-memory/test streams may lack a descriptor; they can still accept events.
        pass
    emit_event(event)


def _optional_event(event: Mapping[str, Any], *, managed: bool = False) -> None:
    """Emit a lifecycle diagnostic independently of durable run correctness."""
    try:
        _emit_diagnostic(event, suppress_fifo=managed)
    except Exception:
        logger.warning("Optimization diagnostic emission failed; sensitive details omitted.")


def _optional_mlflow(operation: Any, *args: Any, **kwargs: Any) -> Any:
    """Keep optional tracking failures separate from required evidence and Store writes."""
    try:
        return operation(*args, **kwargs)
    except Exception:
        logger.warning("Optional optimization tracking failed; sensitive details omitted.")
        return None


@contextmanager
def _tracking_run(name: str) -> Any:
    """Enter and close optional tracking without swallowing errors from the optimization."""
    context = None
    active = None
    try:
        context = mlflow.start_run(run_name=name)
        active = context.__enter__()
    except Exception:
        logger.warning("Optional optimization tracking setup failed; sensitive details omitted.")
        context = None
    try:
        yield active
    finally:
        if context is not None:
            _optional_mlflow(context.__exit__, *sys.exc_info())


def _update_run(
    store: Store,
    run_id: str,
    *,
    owner_id: str | None = None,
    expected_statuses: tuple[str, ...] | None = None,
    **updates: Any,
) -> dict[str, Any] | None:
    """Persist one lifecycle snapshot with an owner/status compare-and-swap."""
    return store.update_optimization_run(
        run_id,
        updates,
        owner_id=owner_id,
        expected_statuses=expected_statuses,
    )
