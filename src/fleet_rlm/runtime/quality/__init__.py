"""DSPy-native evaluation, optimization, and scoring helpers."""

from .dspy_evaluation import evaluate_program, evaluate_program_from_dataset
from .gepa_optimization import build_gepa_feedback_metric, optimize_program_with_gepa
from .mlflow_evaluation import (
    build_default_scorers,
    evaluate_trace_rows,
    export_annotated_trace_rows,
    load_trace_rows,
    rows_with_expected_responses,
    save_evaluation_result,
    serialize_evaluation_result,
)
from .mlflow_optimization import (
    build_exact_match_metric,
    build_program,
    load_symbol,
    optimize_program_with_mipro,
    rows_to_examples,
    split_examples,
)
from .scorers import (
    build_rlm_scorers,
    get_default_judge_model,
    reasoning_quality_scorer,
)
from .workspace_metrics import (
    completeness_feedback_metric,
    exact_match_feedback_metric,
    workspace_feedback_metric,
    workspace_score_metric,
)

__all__ = [
    "build_default_scorers",
    "build_exact_match_metric",
    "build_gepa_feedback_metric",
    "build_program",
    "build_rlm_scorers",
    "completeness_feedback_metric",
    "evaluate_program",
    "evaluate_program_from_dataset",
    "evaluate_trace_rows",
    "exact_match_feedback_metric",
    "export_annotated_trace_rows",
    "get_default_judge_model",
    "load_symbol",
    "load_trace_rows",
    "optimize_program_with_gepa",
    "optimize_program_with_mipro",
    "reasoning_quality_scorer",
    "rows_to_examples",
    "rows_with_expected_responses",
    "save_evaluation_result",
    "serialize_evaluation_result",
    "split_examples",
    "workspace_feedback_metric",
    "workspace_score_metric",
]
