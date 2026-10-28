"""Evaluation and diagnostics for native GEPA candidates."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from statistics import median
from typing import Any

import dspy
import mlflow


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
    ) -> None:
        """Wrap the scored program; failures are counted per thread and re-raised for the evaluator."""
        super().__init__()
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
    tracker = _RolloutTracker(program, trace_context=trace_context, trace_run_id=trace_run_id)
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
    )
    held = score_split(
        program,
        holdout,
        metric,
        threads,
        trace_context=context("holdout"),
        trace_run_id=trace_run_id,
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
