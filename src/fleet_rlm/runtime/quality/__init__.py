"""DSPy-native evaluation, optimization, and scoring helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
        validate_required_keys,
    )
    from .datasets import (
        split_examples as split_dataset_examples,
    )
    from .dspy_evaluation import evaluate_program, evaluate_program_from_dataset
    from .gepa_optimization import (
        build_gepa_feedback_metric,
        optimize_program_with_gepa,
    )
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
    from .module_registry import (
        ModuleOptimizationSpec,
        get_module_spec,
        list_module_metadata,
        list_module_slugs,
        register_module,
    )
    from .optimization_runner import OptimizationResult, run_module_optimization
    from .scorers import (
        build_rlm_scorers,
        get_default_judge_model,
        reasoning_quality_scorer,
    )
    from .scoring_helpers import (
        ScoreFeedbackBuilder,
        action_match_score,
        boundedness_score,
        set_overlap_score,
        text_presence_score,
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
]

_IMPORT_MAP: dict[str, tuple[str, str]] = {
    # artifacts
    "DAYTONA_QUALITY_ROOT": (
        "fleet_rlm.runtime.quality.artifacts",
        "DAYTONA_QUALITY_ROOT",
    ),
    "LOCAL_QUALITY_ROOT": ("fleet_rlm.runtime.quality.artifacts", "LOCAL_QUALITY_ROOT"),
    "build_manifest": ("fleet_rlm.runtime.quality.artifacts", "build_manifest"),
    "resolve_artifact_path": (
        "fleet_rlm.runtime.quality.artifacts",
        "resolve_artifact_path",
    ),
    "write_manifest": ("fleet_rlm.runtime.quality.artifacts", "write_manifest"),
    # datasets
    "DatasetRow": ("fleet_rlm.runtime.quality.datasets", "DatasetRow"),
    "load_dataset_rows": ("fleet_rlm.runtime.quality.datasets", "load_dataset_rows"),
    "split_dataset_examples": ("fleet_rlm.runtime.quality.datasets", "split_examples"),
    "validate_required_keys": (
        "fleet_rlm.runtime.quality.datasets",
        "validate_required_keys",
    ),
    # module_registry
    "ModuleOptimizationSpec": (
        "fleet_rlm.runtime.quality.module_registry",
        "ModuleOptimizationSpec",
    ),
    "get_module_spec": ("fleet_rlm.runtime.quality.module_registry", "get_module_spec"),
    "list_module_metadata": (
        "fleet_rlm.runtime.quality.module_registry",
        "list_module_metadata",
    ),
    "list_module_slugs": (
        "fleet_rlm.runtime.quality.module_registry",
        "list_module_slugs",
    ),
    "register_module": ("fleet_rlm.runtime.quality.module_registry", "register_module"),
    # optimization_runner
    "OptimizationResult": (
        "fleet_rlm.runtime.quality.optimization_runner",
        "OptimizationResult",
    ),
    "run_module_optimization": (
        "fleet_rlm.runtime.quality.optimization_runner",
        "run_module_optimization",
    ),
    # scoring_helpers
    "ScoreFeedbackBuilder": (
        "fleet_rlm.runtime.quality.scoring_helpers",
        "ScoreFeedbackBuilder",
    ),
    "action_match_score": (
        "fleet_rlm.runtime.quality.scoring_helpers",
        "action_match_score",
    ),
    "boundedness_score": (
        "fleet_rlm.runtime.quality.scoring_helpers",
        "boundedness_score",
    ),
    "set_overlap_score": (
        "fleet_rlm.runtime.quality.scoring_helpers",
        "set_overlap_score",
    ),
    "text_presence_score": (
        "fleet_rlm.runtime.quality.scoring_helpers",
        "text_presence_score",
    ),
    # dspy_evaluation
    "evaluate_program": (
        "fleet_rlm.runtime.quality.dspy_evaluation",
        "evaluate_program",
    ),
    "evaluate_program_from_dataset": (
        "fleet_rlm.runtime.quality.dspy_evaluation",
        "evaluate_program_from_dataset",
    ),
    # gepa_optimization
    "build_gepa_feedback_metric": (
        "fleet_rlm.runtime.quality.gepa_optimization",
        "build_gepa_feedback_metric",
    ),
    "optimize_program_with_gepa": (
        "fleet_rlm.runtime.quality.gepa_optimization",
        "optimize_program_with_gepa",
    ),
    # mlflow_evaluation
    "build_default_scorers": (
        "fleet_rlm.runtime.quality.mlflow_evaluation",
        "build_default_scorers",
    ),
    "evaluate_trace_rows": (
        "fleet_rlm.runtime.quality.mlflow_evaluation",
        "evaluate_trace_rows",
    ),
    "export_annotated_trace_rows": (
        "fleet_rlm.runtime.quality.mlflow_evaluation",
        "export_annotated_trace_rows",
    ),
    "load_trace_rows": (
        "fleet_rlm.runtime.quality.mlflow_evaluation",
        "load_trace_rows",
    ),
    "rows_with_expected_responses": (
        "fleet_rlm.runtime.quality.mlflow_evaluation",
        "rows_with_expected_responses",
    ),
    "save_evaluation_result": (
        "fleet_rlm.runtime.quality.mlflow_evaluation",
        "save_evaluation_result",
    ),
    "serialize_evaluation_result": (
        "fleet_rlm.runtime.quality.mlflow_evaluation",
        "serialize_evaluation_result",
    ),
    # mlflow_optimization
    "build_exact_match_metric": (
        "fleet_rlm.runtime.quality.mlflow_optimization",
        "build_exact_match_metric",
    ),
    "build_program": ("fleet_rlm.runtime.quality.mlflow_optimization", "build_program"),
    "load_symbol": ("fleet_rlm.runtime.quality.mlflow_optimization", "load_symbol"),
    "optimize_program_with_mipro": (
        "fleet_rlm.runtime.quality.mlflow_optimization",
        "optimize_program_with_mipro",
    ),
    "rows_to_examples": (
        "fleet_rlm.runtime.quality.mlflow_optimization",
        "rows_to_examples",
    ),
    "split_examples": (
        "fleet_rlm.runtime.quality.mlflow_optimization",
        "split_examples",
    ),
    # scorers
    "build_rlm_scorers": ("fleet_rlm.runtime.quality.scorers", "build_rlm_scorers"),
    "get_default_judge_model": (
        "fleet_rlm.runtime.quality.scorers",
        "get_default_judge_model",
    ),
    "reasoning_quality_scorer": (
        "fleet_rlm.runtime.quality.scorers",
        "reasoning_quality_scorer",
    ),
    # workspace_metrics
    "completeness_feedback_metric": (
        "fleet_rlm.runtime.quality.workspace_metrics",
        "completeness_feedback_metric",
    ),
    "exact_match_feedback_metric": (
        "fleet_rlm.runtime.quality.workspace_metrics",
        "exact_match_feedback_metric",
    ),
    "workspace_feedback_metric": (
        "fleet_rlm.runtime.quality.workspace_metrics",
        "workspace_feedback_metric",
    ),
    "workspace_score_metric": (
        "fleet_rlm.runtime.quality.workspace_metrics",
        "workspace_score_metric",
    ),
}


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module_name, attr_name = _IMPORT_MAP[name]
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
