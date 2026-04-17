"""GEPA (reflective prompt evolution) optimizer integration with MLflow autologging.

Provides :func:`optimize_program_with_gepa` as the primary optimizer for
fleet-rlm programs, complementing the existing MIPROv2 path in
:mod:`.mlflow_optimization`.

GEPA uses *text feedback* from a :class:`GEPAFeedbackMetric` to evolve
prompts, making it especially effective when the metric can explain *why*
a prediction is wrong rather than just returning a scalar score.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

import dspy
from dspy import Example, Prediction
from dspy.teleprompt import GEPA
from dspy.teleprompt.gepa.gepa_utils import ScoreWithFeedback

from fleet_rlm.integrations.observability.config import MlflowConfig
from fleet_rlm.integrations.observability.mlflow_runtime import initialize_mlflow

from .dspy_evaluation import _metric_supports_trace
from .mlflow_evaluation import load_trace_rows
from .mlflow_optimization import (
    build_program,
    rows_to_examples,
    split_examples,
)
from .workspace_metrics import workspace_feedback_metric

logger = logging.getLogger(__name__)

__all__ = [
    "build_gepa_feedback_metric",
    "log_gepa_mlflow_run_metadata",
    "optimize_program_with_gepa",
]


# ---------------------------------------------------------------------------
# Metric adapter
# ---------------------------------------------------------------------------


def build_gepa_feedback_metric(
    *,
    output_key: str = "assistant_response",
    score_fn: Callable[..., float | tuple[float, str]] | None = None,
) -> Callable[..., float | ScoreWithFeedback]:
    """Build a GEPA-compatible feedback metric.

    When *score_fn* is ``None`` the default
    :func:`~.workspace_metrics.workspace_feedback_metric` is used.

    The returned callable conforms to the
    :class:`~dspy.teleprompt.gepa.gepa.GEPAFeedbackMetric` protocol.
    """
    inner = score_fn
    inner_supports_trace = _metric_supports_trace(inner) if inner is not None else False

    def _call_feedback_metric(
        gold: Example,
        pred: Prediction,
        *,
        trace: Any = None,
    ) -> float | tuple[float, str]:
        if inner is None:
            return workspace_feedback_metric(
                gold,
                pred,
                trace=trace,
                output_key=output_key,
            )
        if inner_supports_trace:
            return inner(gold, pred, trace=trace)
        return inner(gold, pred)

    def metric(
        gold: Example,
        pred: Prediction,
        trace: Any = None,
        pred_name: str | None = None,
        pred_trace: Any = None,
    ) -> float | ScoreWithFeedback:
        result = _call_feedback_metric(gold, pred, trace=trace)
        if isinstance(result, tuple) and len(result) == 2:
            score, feedback = result
            return ScoreWithFeedback(score=float(score), feedback=str(feedback))
        return float(result)

    return metric


# ---------------------------------------------------------------------------
# End-to-end GEPA optimization
# ---------------------------------------------------------------------------


def log_gepa_mlflow_run_metadata(
    *,
    dataset_path: Path,
    program_spec: str,
    auto: Literal["light", "medium", "heavy"] | None,
    train_ratio: float,
    module_slug: str | None = None,
    source: str,
    log_params: Callable[[dict[str, Any]], Any] | None = None,
    set_tags: Callable[[dict[str, str]], Any] | None = None,
) -> None:
    """Attach consistent GEPA metadata to the active MLflow run."""

    if log_params is not None:
        cast(Any, log_params)(
            {
                "gepa.auto": auto or "none",
                "gepa.train_ratio": train_ratio,
                "gepa.dataset_name": dataset_path.name,
            }
        )

    tags = {
        "fleet.optimizer": "GEPA",
        "fleet.optimization_source": source,
        "fleet.program_spec": program_spec,
    }
    if module_slug:
        tags["fleet.module_slug"] = module_slug

    if set_tags is not None:
        cast(Any, set_tags)(tags)


def optimize_program_with_gepa(
    *,
    dataset_path: Path,
    program_spec: str,
    output_path: Path | None = None,
    input_keys: list[str] | None = None,
    output_key: str = "assistant_response",
    train_ratio: float = 0.8,
    auto: Literal["light", "medium", "heavy"] | None = "light",
    run_name: str | None = None,
    source: str = "offline",
    config: MlflowConfig | None = None,
    score_fn: Callable[..., float | tuple[float, str]] | None = None,
) -> dict[str, Any]:
    """Run GEPA against an exported MLflow dataset under MLflow autologging.

    Parameters mirror :func:`~.mlflow_optimization.optimize_program_with_mipro`
    for a consistent caller experience.  The key difference is that GEPA uses
    *text feedback* from the metric to guide prompt evolution.

    Returns a summary dict with train/validation counts, output path, and
    validation score.
    """
    import mlflow

    resolved = (config or MlflowConfig.from_env()).model_copy(
        update={
            "dspy_log_traces_from_compile": True,
            "dspy_log_traces_from_eval": True,
            "dspy_log_compiles": True,
            "dspy_log_evals": True,
        }
    )
    if not initialize_mlflow(resolved):
        raise RuntimeError(
            "MLflow optimization is unavailable. Check MLFLOW_* settings."
        )

    rows = load_trace_rows(dataset_path)
    examples = rows_to_examples(
        rows,
        input_keys=input_keys,
        output_key=output_key,
    )
    trainset, valset = split_examples(examples, train_ratio=train_ratio)
    feedback_metric = build_gepa_feedback_metric(
        output_key=output_key,
        score_fn=score_fn,
    )
    program = build_program(program_spec)

    from fleet_rlm.runtime.config import (
        get_delegate_lm_from_env,
        get_planner_lm_from_env,
    )

    reflection_lm = get_delegate_lm_from_env() or get_planner_lm_from_env()
    if reflection_lm is None:
        raise RuntimeError(
            "No DSPy LM configured for GEPA reflection. "
            "Set DSPY_LM_MODEL (and DSPY_LLM_API_KEY) or DSPY_DELEGATE_LM_MODEL."
        )
    optimizer = GEPA(metric=feedback_metric, auto=auto, reflection_lm=reflection_lm)
    resolved_run_name = run_name or f"GEPA::{program_spec}"
    start_run = getattr(mlflow, "start_run", None)
    log_metric = getattr(mlflow, "log_metric", None)
    log_params = getattr(mlflow, "log_params", None)
    set_tags = getattr(mlflow, "set_tags", None)
    if start_run is None or log_metric is None:
        raise RuntimeError(
            "MLflow tracking helpers are unavailable in this environment."
        )

    with cast(Any, start_run)(run_name=resolved_run_name):
        log_gepa_mlflow_run_metadata(
            dataset_path=dataset_path,
            program_spec=program_spec,
            auto=auto,
            train_ratio=train_ratio,
            source=source,
            log_params=cast(Any, log_params),
            set_tags=cast(Any, set_tags),
        )
        optimized = optimizer.compile(
            program,
            trainset=trainset,
            valset=valset or None,
        )
        cast(Any, log_metric)("gepa_train_examples", len(trainset))
        cast(Any, log_metric)("gepa_validation_examples", len(valset))
        validation_score = None
        if valset:
            evaluator = dspy.Evaluate(devset=valset, metric=feedback_metric)
            validation_result = evaluator(optimized)
            validation_score = float(validation_result)
            cast(Any, log_metric)("gepa_validation_score", validation_score)

        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            optimized.save(str(output_path))

    logger.info(
        "GEPA optimization complete: train=%d val=%d score=%s",
        len(trainset),
        len(valset),
        validation_score,
    )
    return {
        "train_examples": len(trainset),
        "validation_examples": len(valset),
        "output_path": str(output_path) if output_path is not None else None,
        "validation_score": validation_score,
        "program_spec": program_spec,
        "optimizer": "GEPA",
    }
