"""Evaluation and diagnostics for native GEPA candidates."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager, suppress
from copy import deepcopy
from dataclasses import dataclass
from statistics import median
from typing import Any

import dspy
import mlflow

from fleet_rlm.config import FleetSettings
from fleet_rlm.contracts import ENGINE_INPUT_FIELDS, OptimizationInputs
from fleet_rlm.engine.invocation import FleetRuntime
from fleet_rlm.engine.programs import FleetProgram
from fleet_rlm.engine.reasoning import deserialize_rlm_history
from fleet_rlm.models import build_lm
from fleet_rlm.replay import required_skills_from_snapshot, selected_snapshot
from fleet_rlm.sandbox.manager import SANDBOX_TRANSPORT, DaytonaSandboxManager
from fleet_rlm.store import Store
from fleet_rlm.workspace import WorkspaceDataset, validate_session_id


class RolloutCleanupError(RuntimeError):
    """An isolated runtime did not confirm cleanup; its run cannot certify evidence."""


class RolloutCleanup:
    """Keep cleanup failure and admitted attempts outside native DSPy scoring semantics."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active = 0
        self._closed = False
        self._unconfirmed: list[DaytonaSandboxManager] = []

    def __deepcopy__(self, memo: dict[int, Any]) -> RolloutCleanup:
        # Native candidate copies share the run's owner, never copies of its locks or latch.
        memo[id(self)] = self
        return self

    def should_stop(self, *_args: Any, **_kwargs: Any) -> bool:
        """Ask native GEPA to stop cooperatively after cleanup uncertainty."""
        with self._condition:
            return bool(self._unconfirmed)

    def raise_if_failed(self) -> None:
        """Reject evidence even when DSPy converted an exception to a failure score."""
        if self.should_stop():
            raise RolloutCleanupError("Isolated sandbox cleanup is unconfirmed; run evidence is invalid")

    @contextmanager
    def attempt(self) -> Generator[None]:
        """Admit one attempt through setup, execution and cleanup, without serializing work."""
        with self._condition:
            self.raise_if_failed()
            if self._closed:
                raise RolloutCleanupError("Isolated rollout admission is closed")
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def shutdown(self, manager: DaytonaSandboxManager, error: BaseException | None = None) -> None:
        """Confirm cleanup once, retaining uncertain identities without masking a primary error."""
        interruption = None
        try:
            confirmed = manager.shutdown() is True
        except BaseException as cleanup_error:
            confirmed = False
            if not isinstance(cleanup_error, Exception):
                interruption = cleanup_error
        if confirmed:
            return
        with self._condition:
            self._unconfirmed.append(manager)
        # Keep the existing allocation journals; these identities locate them for recovery.
        logging.getLogger(__name__).warning(
            "Isolated cleanup unconfirmed for Session %s agent %s; allocation journal retained",
            manager.session_id,
            manager.agent_id,
        )
        if error is not None:
            error.add_note("Isolated sandbox cleanup is unconfirmed; allocation journal retained")
            return
        if interruption is not None:
            raise interruption
        self.raise_if_failed()

    def close(self) -> None:
        """Close admission and drain admitted attempts before propagating an interrupt."""
        interruption = None
        while True:
            try:
                with self._condition:
                    self._closed = True
                    self._condition.wait_for(lambda: self._active == 0)
                break
            except KeyboardInterrupt as error:
                if interruption is None:
                    interruption = error
        if interruption is not None:
            raise interruption

    @contextmanager
    def run(self) -> Generator[None]:
        """Drain before worker finalization, preserving the original failure or cancellation."""
        try:
            yield
        except BaseException as error:
            try:
                self.close()
            except KeyboardInterrupt:
                error.add_note("SIGINT received during drain; admitted rollouts finished before propagation")
            if self.should_stop():
                error.add_note("Isolated sandbox cleanup is unconfirmed; run evidence is invalid")
            raise
        else:
            self.close()
            self.raise_if_failed()


class IsolatedFleetProgram(dspy.Module):
    """Expose candidate predictors while giving every rollout a private runtime."""

    def __init__(self, program: dspy.Module, engine_factory: Any, cleanup: RolloutCleanup | None = None):
        """Wrap the program so each forward call builds its own engine."""
        super().__init__()
        self.program = program
        self.engine_factory = engine_factory
        self.cleanup = cleanup if cleanup is not None else RolloutCleanup()

    def load_state(self, state: dict[str, Any], *, allow_unsafe_lm_state: bool = False) -> None:
        """Validate the complete underlying graph, rather than bypassing it parameter by parameter."""
        if not isinstance(self.program, FleetProgram):
            # Native Parameter students (such as Flex) serialize under `program`, not dotted fields.
            super().load_state(state, allow_unsafe_lm_state=allow_unsafe_lm_state)
            return
        if set(state) != {f"program.{key}" for key in self.program.dump_state()}:
            raise ValueError("Isolated candidate requires complete native program-prefixed state")
        self.program.load_state(
            {key.removeprefix("program."): value for key, value in state.items()},
            allow_unsafe_lm_state=allow_unsafe_lm_state,
        )

    def forward(self, **inputs: Any) -> dspy.Prediction:
        """Run one rollout in a fresh engine and always shut that engine's sandbox down."""
        with self.cleanup.attempt():
            engine = self.engine_factory(dspy.Example(**inputs), self.program)
            error = None
            try:
                recorder = dspy.settings.get("_decision_evidence")
                arguments = {name: value for name, value in inputs.items() if name in ENGINE_INPUT_FIELDS}
                if engine.optimization is not None:
                    arguments["context"] = engine.optimization.context
                    arguments["rlm_history"] = engine.optimization.rlm_history
                    arguments["required_skills"] = engine.optimization.required_skills
                result = engine(**arguments)
                if recorder is not None:
                    log, lock = recorder
                    outer_by_name = dict(self.program.named_parameters())
                    remap = {
                        inner_param: outer_by_name[param_name]
                        for param_name, inner_param in engine.program.named_parameters()
                        if param_name in outer_by_name
                    }
                    if remap:
                        with lock:
                            for idx, (caller, field_name, evidence) in enumerate(log):
                                if caller in remap:
                                    log[idx] = (remap[caller], field_name, evidence)
                return result
            except BaseException as exc:
                error = exc
                raise
            finally:
                self.cleanup.shutdown(engine.sandbox_mgr, error)


def build_isolated_engine_factory(
    settings: FleetSettings,
    store: Store,
    examples: Any = (),
    *,
    cleanup: RolloutCleanup | None = None,
    cache_decisions: bool = False,
) -> Any:
    """Return a factory that builds disposable replay engines with verified immutable inputs.

    Each fresh engine receives an independent copy of the candidate's native state.
    Serving LMs are shared within this factory; callers own cleanup of returned engines.
    Supply the same cleanup owner to this factory and its rollout wrappers for a run.
    """
    from fleet_rlm.execution_evidence import verification_entries

    verifications = {
        (item.source_session, item.replay_turn_id, item.replay_snapshot_sha256): verification_entries(
            getattr(item, "structured_expectations", {}) or {}
        )
        for item in examples
    }

    cleanup = cleanup if cleanup is not None else RolloutCleanup()
    shared: list[Any] = []
    shared_lock = threading.Lock()

    def language_models() -> tuple[Any, Any, Any]:
        """Build the serving LMs once per factory; rollouts share them instead of rebuilding per example."""
        with shared_lock:
            if not shared:
                root = build_lm(settings, settings.fleet_root_model, "root")
                sub = build_lm(settings, settings.fleet_sub_model, "sub")
                jev = settings.build_jev_lm()
                if cache_decisions and jev is not None and callable(getattr(jev, "copy", None)):
                    jev = jev.copy(cache=True)
                shared.extend((root, sub, jev))
            return shared[0], shared[1], shared[2]

    def create(example: dspy.Example, program: FleetProgram) -> FleetRuntime:
        """Restore one reviewed replay into a disposable engine using the candidate program.

        Reject inconsistent replay metadata with ValueError. Setup failures trigger
        sandbox shutdown; no partially loaded engine is returned.
        """
        snapshot = selected_snapshot(
            store, example.source_session, example.replay_turn_id, example.replay_snapshot_sha256
        )
        if any(snapshot[name] != getattr(example, name) for name in (*ENGINE_INPUT_FIELDS, "context_files")):
            raise ValueError("Curated replay snapshot does not match the reviewed example")
        if snapshot.get("requested_skills", []) != getattr(example, "requested_skills", []):
            raise ValueError("Curated required Skill intent does not match the reviewed example")
        required_skills = required_skills_from_snapshot(snapshot)
        if snapshot.get("sandbox_transport") != SANDBOX_TRANSPORT:
            raise ValueError("Curated replay snapshot uses an unsupported sandbox transport")
        if not isinstance(program, FleetProgram):
            raise TypeError("Isolated rollout requires an unwrapped FleetProgram candidate")
        root_lm, sub_lm, jev_lm = language_models()
        replay_context = snapshot.get("dataset_context")
        replay_files = snapshot["replay_files"]
        namespace = validate_session_id(f"opt-{uuid.uuid4().hex}")
        manager = DaytonaSandboxManager(
            api_key=settings.fleet_daytona_api_key,
            org_id=settings.fleet_daytona_org_id,
            volume_id=settings.fleet_daytona_volume_id,
            session_id=namespace,
            auto_stop_minutes=settings.daytona_auto_stop_minutes,
            sub_lm=sub_lm,
            jev_lm=jev_lm,
            max_llm_calls=settings.max_llm_calls_per_turn,
            max_depth=settings.max_agent_depth,
            max_children=settings.max_child_sandboxes,
            max_active_children=settings.max_active_children,
            turn_timeout_seconds=settings.turn_timeout_seconds,
            linked_children=settings.linked_children,
            disposable=True,
        )
        try:
            for path, encoded in replay_files.items():
                content = base64.b64decode(encoded, validate=True)
                manager.write_workspace_file(path, content)
            engine = FleetRuntime(
                sandbox_mgr=manager,
                root_lm=root_lm,
                sub_lm=sub_lm,
                jev_lm=jev_lm,
                compiled_path=None,
                execution_mode=settings.execution_mode,
                root_adapter=settings.resolved_model("root").build_adapter(),
                sub_adapter=settings.resolved_model("sub").build_adapter(),
            )
            engine.program.load_state(deepcopy(program.dump_state()))
            engine.optimization = OptimizationInputs(
                rlm_history=deserialize_rlm_history(snapshot.get("rlm_history", [])),
                required_skills=required_skills,
                context=WorkspaceDataset(**replay_context) if replay_context else None,
                verifications=tuple(
                    verifications.get(
                        (example.source_session, example.replay_turn_id, example.replay_snapshot_sha256), []
                    )
                ),
            )
            return engine
        except BaseException as error:
            cleanup.shutdown(manager, error)
            raise

    return create


@dataclass(frozen=True, slots=True)
class SplitScore:
    """Measured scores, usage, artifact outcomes, and timing for one example split."""

    mean: float
    errors: int
    scores: list[float]
    critical_scores: list[float]
    objectives: dict[str, float]
    lm_calls: list[int | None]
    seconds_per_example: list[float]
    artifact_passes: int
    artifact_cases: int
    failures: list[dict[str, str]]
    seconds: float


class _RolloutTracker(dspy.Module):
    """Count rollouts that raised, instead of inferring it from the prediction `dspy.Evaluate` substitutes."""

    def __init__(
        self,
        program: dspy.Module,
        *,
        trace_context: Mapping[str, str] | None = None,
        trace_run_id: str | None = None,
        on_rollout: Callable[[], None] | None = None,
    ) -> None:
        """Wrap the scored program; failures are counted per thread and re-raised for the evaluator."""
        super().__init__()
        self.on_rollout = on_rollout
        self.program = program
        self.error_count = 0
        self.failures: list[dict[str, str]] = []
        self.seconds_per_example: list[float] = []
        self._lock = threading.Lock()
        self.trace_context = dict(trace_context or {})
        self.trace_run_id = trace_run_id

    def forward(self, **inputs: Any) -> Any:
        """Run one rollout, recording sanitized failures and timing each attempt."""
        started = time.perf_counter()
        case_id = hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()[:12]
        try:
            if not self.trace_context:
                return self.program(**inputs)
            attributes = {**self.trace_context, "fleet.optimization.case_id": case_id}
            error: Exception | None = None
            result: Any = None
            run_id = self.trace_run_id if mlflow.get_current_active_span() is None else None
            with mlflow.start_span(
                name="fleet.gepa.validation", span_type="CHAIN", attributes=attributes, run_id=run_id
            ) as span:
                span.set_inputs({"case_id": case_id})
                try:
                    result = self.program(**inputs)
                    span.set_outputs({"completed": True})
                except Exception as exc:
                    error = exc
                    span.set_status("ERROR")
                    span.set_outputs({"error_type": type(exc).__name__})
            if error is not None:
                raise error
            return result
        except Exception as error:
            failure = {
                "case_id": case_id,
                "type": type(error).__name__,
                "detail": "Evaluation failed; exception details omitted.",
            }
            with self._lock:
                self.error_count += 1
                self.failures.append(failure)
            raise
        finally:
            with self._lock:
                self.seconds_per_example.append(round(time.perf_counter() - started, 6))
            if self.on_rollout is not None:
                with suppress(Exception):
                    self.on_rollout()


class _SanitizeDSPyErrorLog(logging.Filter):
    """Keep DSPy's parallel evaluator from logging raw rollout exceptions."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Replace messages at ERROR or above and exception details with a generic message; retain every record."""
        if record.levelno >= logging.ERROR:
            record.msg = "Optimization rollout failed; a sanitized case record was saved."
            record.args = ()
            record.exc_info = None
            record.exc_text = None
        return True


def score_split(
    program: dspy.Module,
    examples: list[dspy.Example],
    metric: Any,
    threads: int,
    *,
    trace_context: Mapping[str, str] | None = None,
    trace_run_id: str | None = None,
    on_rollout: Callable[[], None] | None = None,
) -> SplitScore:
    """Score every example with ``dspy.Evaluate``; a rollout that raises scores 0.0 and counts as an error.

    Return scores, available usage, artifact outcomes, and elapsed times in seconds.
    Per-example timings are collected as attempts finish, independently of score order.
    Evaluator setup and result processing errors propagate.

    Raises:
        ValueError: examples is empty.
    """
    if not examples:
        raise ValueError("validation split must not be empty")
    started = time.perf_counter()
    tracker = _RolloutTracker(program, trace_context=trace_context, trace_run_id=trace_run_id, on_rollout=on_rollout)
    dspy_error_logger = logging.getLogger("dspy.utils.parallelizer")
    sanitizer = _SanitizeDSPyErrorLog()
    dspy_error_logger.addFilter(sanitizer)
    try:
        result = dspy.Evaluate(
            devset=examples,
            metric=metric,
            num_threads=threads,
            failure_score=0.0,
            max_errors=len(examples) + 1,
            provide_traceback=False,
        )(tracker)
    finally:
        dspy_error_logger.removeFilter(sanitizer)
    scores = [float(score) for _, _, score in result.results]
    errors = tracker.error_count
    lm_calls = [
        calls if type(calls := getattr(prediction, "lm_calls", None)) is int and calls >= 0 else None
        for _, prediction, _ in result.results
    ]
    artifact_scores = [
        (getattr(score, "objective_scores", None) or {}).get("artifact_correctness") for _, _, score in result.results
    ]
    applicable_artifact_scores = [value for value in artifact_scores if isinstance(value, (int, float))]
    artifact_passes = sum(value >= 1.0 for value in applicable_artifact_scores)
    critical_scores = [
        float((getattr(score, "objective_scores", None) or {}).get("quality", float(score)))
        for example, _prediction, score in result.results
        if getattr(example, "critical", False) is True
    ]
    objective_names = {"quality", "usage_available"}
    for _, _, score in result.results:
        objective_names.update((getattr(score, "objective_scores", None) or {}).keys())
    objectives = {
        name: sum(
            float((getattr(score, "objective_scores", None) or {}).get(name, 0.0)) for _, _, score in result.results
        )
        / len(examples)
        for name in objective_names
    }
    return SplitScore(
        mean=sum(scores) / max(1, len(scores)),
        errors=errors,
        scores=scores,
        critical_scores=critical_scores,
        objectives=objectives,
        lm_calls=lm_calls,
        seconds_per_example=tracker.seconds_per_example,
        artifact_passes=artifact_passes,
        artifact_cases=len(applicable_artifact_scores),
        failures=tracker.failures,
        seconds=round(time.perf_counter() - started, 1),
    )


def score_stage(
    program: dspy.Module,
    devset: list[dspy.Example],
    holdout: list[dspy.Example],
    metric: Any,
    threads: int,
    *,
    trace_run_id: str | None = None,
    stage: str | None = None,
    pass_index: int | None = None,
    on_rollout: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Score the dev and holdout splits and return the combined metrics for one GEPA stage."""

    def context(split: str) -> dict[str, str] | None:
        """Return validation trace tags only when both stage and pass index are supplied."""
        if stage is None or pass_index is None:
            return None
        return {
            "fleet.optimization.stage": stage,
            "fleet.optimization.split": split,
            "fleet.optimization.pass": str(pass_index),
        }

    dev = score_split(
        program,
        devset,
        metric,
        threads,
        trace_context=context("dev"),
        trace_run_id=trace_run_id,
        on_rollout=on_rollout,
    )
    held = score_split(
        program,
        holdout,
        metric,
        threads,
        trace_context=context("holdout"),
        trace_run_id=trace_run_id,
        on_rollout=on_rollout,
    )
    result: dict[str, Any] = {"seconds": round(dev.seconds + held.seconds, 1)}
    for prefix, split in (("dev", dev), ("holdout", held)):
        result[prefix] = split.mean
        for field in (
            "errors",
            "scores",
            "critical_scores",
            "objectives",
            "lm_calls",
            "seconds_per_example",
            "artifact_passes",
            "artifact_cases",
            "failures",
        ):
            result[f"{prefix}_{field}"] = getattr(split, field)
    return result


def summarize_passes(passes: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep every observation and provide report summaries without dropping failed examples."""
    summary: dict[str, Any] = {"passes": passes, "seconds": sum(item["seconds"] for item in passes)}
    for split in ("dev", "holdout"):
        summary[split] = sum(item[split] for item in passes) / len(passes)
        summary[f"{split}_errors"] = sum(item[f"{split}_errors"] for item in passes)
        summary[f"{split}_scores"] = [
            sum(values) / len(values) for values in zip(*(item[f"{split}_scores"] for item in passes), strict=True)
        ]
        summary[f"{split}_critical_scores"] = [value for item in passes for value in item[f"{split}_critical_scores"]]
        names = set().union(*(item[f"{split}_objectives"] for item in passes))
        summary[f"{split}_objectives"] = {
            name: sum(item[f"{split}_objectives"].get(name, 0) for item in passes) / len(passes) for name in names
        }
        calls = [value for item in passes for value in item.get(f"{split}_lm_calls", [])]
        measured_calls = [value for value in calls if type(value) is int and value >= 0]
        timings = [value for item in passes for value in item.get(f"{split}_seconds_per_example", [])]
        summary[f"{split}_lm_calls"] = calls
        summary[f"{split}_lm_calls_measured"] = len(measured_calls)
        summary[f"{split}_lm_calls_median"] = median(measured_calls) if measured_calls else None
        summary[f"{split}_seconds_per_example"] = timings
        summary[f"{split}_seconds_per_example_median"] = median(timings) if timings else None
        summary[f"{split}_artifact_passes"] = sum(item.get(f"{split}_artifact_passes", 0) for item in passes)
        summary[f"{split}_artifact_cases"] = sum(item.get(f"{split}_artifact_cases", 0) for item in passes)
        summary[f"{split}_failures"] = [failure for item in passes for failure in item.get(f"{split}_failures", [])]
    return summary
