"""DSPy-native evaluation pipeline alongside existing MLflow evaluation.

Provides :func:`evaluate_program` for systematic program assessment using
:class:`dspy.Evaluate`, complementing the MLflow GenAI scorer pipeline in
:mod:`.mlflow_evaluation`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import dspy

from .mlflow_evaluation import load_trace_rows
from .mlflow_optimization import (
    build_exact_match_metric,
    rows_to_examples,
    split_examples,
)
from .workspace_metrics import workspace_feedback_metric

logger = logging.getLogger(__name__)

__all__ = [
    "evaluate_program",
    "evaluate_program_from_dataset",
]


def evaluate_program(
    program: dspy.Module,
    devset: list[dspy.Example],
    *,
    metric: Callable[..., Any] | None = None,
    num_threads: int = 4,
    display_progress: bool = True,
    display_table: int | bool = 0,
    return_all_scores: bool = False,
    return_outputs: bool = False,
) -> dict[str, Any]:
    """Evaluate a DSPy program against a devset using :class:`dspy.Evaluate`.

    Parameters
    ----------
    program:
        The DSPy module to evaluate.
    devset:
        List of :class:`dspy.Example` instances to evaluate against.
    metric:
        Scoring function. Defaults to :func:`~.workspace_metrics.workspace_feedback_metric`.
    num_threads:
        Parallel evaluation threads.
    display_progress:
        Show a progress bar during evaluation.
    display_table:
        Number of rows to display in the results table (0 to disable).
    return_all_scores:
        If True, include per-example scores in the result.
    return_outputs:
        If True, include per-example outputs in the result.

    Returns
    -------
    dict with ``score``, ``num_examples``, and optionally ``all_scores``
    and ``outputs``.
    """
    resolved_metric = metric or workspace_feedback_metric

    evaluator = dspy.Evaluate(
        devset=devset,
        metric=resolved_metric,
        num_threads=num_threads,
        display_progress=display_progress,
        display_table=display_table,
        return_all_scores=return_all_scores,
        return_outputs=return_outputs,
    )

    raw_result = evaluator(program)

    # dspy.Evaluate returns different shapes depending on flags
    if return_all_scores and return_outputs:
        overall_score, all_results, all_scores = raw_result
    elif return_all_scores:
        overall_score, all_scores = raw_result
        all_results = None
    elif return_outputs:
        overall_score, all_results = raw_result
        all_scores = None
    else:
        overall_score = raw_result
        all_scores = None
        all_results = None

    result: dict[str, Any] = {
        "score": float(overall_score),
        "num_examples": len(devset),
        "metric": getattr(resolved_metric, "__name__", str(resolved_metric)),
    }
    if all_scores is not None:
        result["all_scores"] = all_scores
    if all_results is not None:
        result["outputs"] = all_results

    logger.info(
        "DSPy evaluation complete: score=%.4f examples=%d",
        result["score"],
        len(devset),
    )
    return result


def evaluate_program_from_dataset(
    *,
    program: dspy.Module,
    dataset_path: Path,
    input_keys: list[str] | None = None,
    output_key: str = "assistant_response",
    train_ratio: float = 0.0,
    metric: Callable[..., Any] | None = None,
    num_threads: int = 4,
) -> dict[str, Any]:
    """Load an exported MLflow dataset and evaluate a program against it.

    When *train_ratio* is 0.0 (default), the entire dataset is used for
    evaluation.  Otherwise the validation split is used.
    """
    rows = load_trace_rows(dataset_path)
    examples = rows_to_examples(
        rows,
        input_keys=input_keys,
        output_key=output_key,
    )

    if train_ratio > 0.0:
        _, devset = split_examples(examples, train_ratio=train_ratio)
        if not devset:
            devset = examples
    else:
        devset = examples

    if not devset:
        raise ValueError("No evaluation examples produced from the dataset.")

    resolved_metric = metric
    if resolved_metric is None:
        resolved_metric = build_exact_match_metric(output_key)

    return evaluate_program(
        program,
        devset,
        metric=resolved_metric,
        num_threads=num_threads,
    )
