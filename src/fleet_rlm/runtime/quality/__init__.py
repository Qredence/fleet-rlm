"""DSPy-native evaluation, optimization, and scoring helpers."""

# -- Shared infrastructure (new) --------------------------------------------
from .artifacts import (
    DAYTONA_QUALITY_ROOT,
    LOCAL_QUALITY_ROOT,
    build_manifest,
    resolve_artifact_path,
    write_manifest,
)
from .datasets import (
    DatasetRow,
    load_dataset_rows,
    split_examples as split_dataset_examples,
    validate_required_keys,
)
from .module_registry import (
    ModuleOptimizationSpec,
    get_module_spec,
    list_module_metadata,
    list_module_slugs,
    register_module,
)
from .optimization_runner import OptimizationResult, run_module_optimization
from .scoring_helpers import (
    ScoreFeedbackBuilder,
    action_match_score,
    boundedness_score,
    set_overlap_score,
    text_presence_score,
)

# -- Existing infrastructure -------------------------------------------------
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

# -- Per-module entrypoints --------------------------------------------------
from .optimize_reflect_and_revise import (
    build_reflection_feedback_metric,
    load_reflection_rows,
    optimize_reflect_and_revise_module,
    resolve_reflection_output_path,
    rows_to_reflection_examples,
)
from .optimize_recursive_context_selection import (
    build_recursive_context_selection_feedback_metric,
    load_recursive_context_selection_rows,
    optimize_recursive_context_selection_module,
    resolve_recursive_context_selection_output_path,
    rows_to_recursive_context_selection_examples,
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
    # Shared infrastructure
    "DAYTONA_QUALITY_ROOT",
    "DatasetRow",
    "LOCAL_QUALITY_ROOT",
    "ModuleOptimizationSpec",
    "OptimizationResult",
    "ScoreFeedbackBuilder",
    "action_match_score",
    "boundedness_score",
    "build_manifest",
    "get_module_spec",
    "list_module_metadata",
    "list_module_slugs",
    "load_dataset_rows",
    "register_module",
    "resolve_artifact_path",
    "run_module_optimization",
    "set_overlap_score",
    "split_dataset_examples",
    "text_presence_score",
    "validate_required_keys",
    "write_manifest",
    # Existing infrastructure
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
    # Per-module entrypoints
    "build_recursive_context_selection_feedback_metric",
    "build_reflection_feedback_metric",
    "load_recursive_context_selection_rows",
    "load_reflection_rows",
    "optimize_recursive_context_selection_module",
    "optimize_reflect_and_revise_module",
    "resolve_recursive_context_selection_output_path",
    "resolve_reflection_output_path",
    "rows_to_recursive_context_selection_examples",
    "rows_to_reflection_examples",
]
