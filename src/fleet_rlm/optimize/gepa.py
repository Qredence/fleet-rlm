"""`fleet optimize`: score a baseline, compile a GEPA candidate, gate it, and install it on human approval."""

from __future__ import annotations

import argparse

import dspy
import mlflow

from fleet_rlm.observability import configure_mlflow_observability
from fleet_rlm.optimization_runs import ExperimentOptions, emit_event
from fleet_rlm.optimize.artifacts import (
    APPROVAL_POLICY_VERSION,
    component_diffs,
    format_report,
    write_manifest,
)
from fleet_rlm.optimize.cli_commands import (
    _active_program_path,
    _check_readiness,
    _finalize_optimization_run,
    _open_optimization_store,
    _run_approval,
    run,
)
from fleet_rlm.optimize.dataset import curate_dataset, dataset_digest, labels_of, load_examples
from fleet_rlm.optimize.progress import (
    BudgetExhaustedError,
    GEPAProgressReporter,
    JudgeFailedError,
    OptimizationCancelledError,
    OptimizationPersistenceError,
    OptimizationStopPolicy,
    TokenBudget,
    _check_cancelled,
    _database_stop_requested,
    _DatabaseStopper,
    _emit_diagnostic,
    _JudgeLatch,
    _optional_event,
    _optional_mlflow,
    _tracking_run,
    _update_run,
    build_proposal_stop_callback,
    build_stop_file_callback,
    failure_detail,
    failure_reason,
)
from fleet_rlm.optimize.runner import (
    INSTRUCTION_MAX_CHARS,
    _comparison_rationale,
    _compile,
    _counting,
    _curated_proposer_skills,
    _enable_validation_tracing,
    _evaluate,
    _example_trace,
    _judged_metric,
    _replay,
    _rescore,
    _serving_runtime,
    _synthetic,
    build_instruction_proposer,
)
from fleet_rlm.optimize.validation import (
    build_isolated_engine_factory,
    score_split,
    score_stage,
    summarize_passes,
)
from fleet_rlm.store import Store

__all__ = [
    "APPROVAL_POLICY_VERSION",
    "INSTRUCTION_MAX_CHARS",
    "BudgetExhaustedError",
    "ExperimentOptions",
    "GEPAProgressReporter",
    "JudgeFailedError",
    "OptimizationCancelledError",
    "OptimizationPersistenceError",
    "OptimizationStopPolicy",
    "Store",
    "TokenBudget",
    "_DatabaseStopper",
    "_JudgeLatch",
    "_active_program_path",
    "_check_cancelled",
    "_check_readiness",
    "_comparison_rationale",
    "_compile",
    "_counting",
    "_curated_proposer_skills",
    "_database_stop_requested",
    "_emit_diagnostic",
    "_enable_validation_tracing",
    "_evaluate",
    "_example_trace",
    "_finalize_optimization_run",
    "_judged_metric",
    "_open_optimization_store",
    "_optional_event",
    "_optional_mlflow",
    "_replay",
    "_rescore",
    "_run_approval",
    "_serving_runtime",
    "_synthetic",
    "_tracking_run",
    "_update_run",
    "argparse",
    "build_instruction_proposer",
    "build_isolated_engine_factory",
    "build_proposal_stop_callback",
    "build_stop_file_callback",
    "component_diffs",
    "configure_mlflow_observability",
    "curate_dataset",
    "dataset_digest",
    "dspy",
    "emit_event",
    "failure_detail",
    "failure_reason",
    "format_report",
    "labels_of",
    "load_examples",
    "mlflow",
    "run",
    "score_split",
    "score_stage",
    "summarize_passes",
    "write_manifest",
]
