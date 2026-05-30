"""Pydantic request/response schemas for the FastAPI server."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GEPAOptimizationRequest(BaseModel):
    """Request body for triggering a GEPA prompt optimization run."""

    model_config = ConfigDict(extra="forbid")

    dataset_path: str | None = Field(
        default=None,
        description="Relative filesystem path to the dataset file.",
    )
    dataset_id: str | None = Field(
        default=None,
        description="Registered dataset identifier to optimize against.",
    )
    program_spec: str = Field(
        default="",
        description="DSPy program specification string to optimize in module:attr form. "
        "Required when module_slug is not provided.",
    )
    module_slug: str | None = Field(
        default=None,
        description="Registered module slug for server-side dispatch. "
        "When provided, program_spec is auto-resolved from the module registry.",
    )
    output_path: str | None = Field(
        default=None,
        description="Optional filesystem path to save the optimized program.",
    )
    auto: Literal["light", "medium", "heavy"] = Field(
        default="light",
        description="GEPA optimization intensity level.",
    )
    train_ratio: float = Field(
        default=0.8,
        description="Fraction of examples to use for training (remainder used for validation).",
    )

    @model_validator(mode="after")
    def validate_dataset_target(self) -> GEPAOptimizationRequest:
        if self.dataset_id is not None or (self.dataset_path or "").strip():
            return self
        raise ValueError("dataset_id or dataset_path is required")


class GEPAOptimizationResponse(BaseModel):
    """Result payload after a GEPA optimization run completes."""

    ok: bool = Field(
        default=True,
        description="Whether the optimization run completed successfully.",
    )
    optimizer: str = Field(
        default="GEPA",
        description="Optimizer backend that was used.",
    )
    program_spec: str = Field(
        description="DSPy program specification that was optimized.",
    )
    train_examples: int = Field(
        description="Number of training examples used.",
    )
    validation_examples: int = Field(
        description="Number of validation examples used.",
    )
    validation_score: float | None = Field(
        default=None,
        description="Validation score from the optimized program, when available.",
    )
    output_path: str | None = Field(
        default=None,
        description="Filesystem path where the optimized program was saved.",
    )
    manifest_path: str | None = Field(
        default=None,
        description="Filesystem path to the optimization manifest, when available.",
    )
    module_slug: str | None = Field(
        default=None,
        description="Module slug used for this optimization run, when server-side dispatch was used.",
    )
    error: str | None = Field(
        default=None,
        description="Error message when the optimization run failed.",
    )


class GEPAModuleInfo(BaseModel):
    """Metadata for a registered optimizable DSPy module."""

    slug: str = Field(description="Unique module identifier slug.")
    label: str = Field(description="Human-readable module label.")
    description: str = Field(
        default="",
        description="Human-readable description of what this module optimizes.",
    )
    program_spec: str = Field(description="DSPy program specification string.")
    required_dataset_keys: list[str] = Field(description="Dataset keys required for this module's examples.")
    offline_only: bool = Field(
        default=True,
        description="Whether this module can only be optimized through offline optimization endpoints.",
    )


class GEPAStatusResponse(BaseModel):
    """Status payload for GEPA optimization availability."""

    available: bool = Field(
        description="Whether the MLflow-backed GEPA optimization path is available.",
    )
    module_optimization_available: bool = Field(
        default=False,
        description="Whether registered module optimization can run in this environment.",
    )
    mlflow_dataset_optimization_available: bool = Field(
        default=False,
        description="Whether MLflow-backed dataset/program optimization can run.",
    )
    mlflow_logging_available: bool = Field(
        default=False,
        description="Whether optional MLflow logging is available for optimization runs.",
    )
    mlflow_configured: bool = Field(
        default=False,
        description="Whether MLflow is enabled/configured in the environment.",
    )
    mlflow_enabled: bool = Field(
        description="Whether MLflow is enabled and reachable.",
    )
    gepa_installed: bool = Field(
        description="Whether the GEPA teleprompt module is importable.",
    )
    guidance: list[str] = Field(
        default_factory=list,
        description="Human-readable guidance when GEPA is not fully available.",
    )


class OptimizationRunResponse(BaseModel):
    """A single optimization run record."""

    id: str = Field(description="Unique run identifier.")
    status: str = Field(description="Run status: running, completed, or failed.")
    module_slug: str | None = Field(default=None, description="Module slug when server-side dispatch was used.")
    program_spec: str = Field(description="DSPy program specification that was optimized.")
    optimizer: str = Field(description="Optimizer backend that was used.")
    auto: str | None = Field(default="light", description="Optimization intensity level.")
    train_ratio: float = Field(default=0.8, description="Train/validation split ratio.")
    dataset_path: str | None = Field(default=None, description="Path to the dataset used.")
    train_examples: int | None = Field(default=None, description="Number of training examples used.")
    validation_examples: int | None = Field(default=None, description="Number of validation examples used.")
    validation_score: float | None = Field(default=None, description="Validation score from the optimized program.")
    output_path: str | None = Field(
        default=None,
        description="Filesystem path where the optimized program was saved.",
    )
    manifest_path: str | None = Field(default=None, description="Filesystem path to the optimization manifest.")
    error: str | None = Field(default=None, description="Error message when the run failed.")
    phase: str | None = Field(default=None, description="Current phase of the optimization run.")
    started_at: str = Field(description="ISO timestamp when the run started.")
    completed_at: str | None = Field(default=None, description="ISO timestamp when the run completed.")


class OptimizationRunCreatedResponse(BaseModel):
    """Response when an async optimization run is created."""

    run_id: str = Field(description="Unique identifier for the created run.")
    status: str = Field(default="running", description="Initial run status.")


class DatasetResponse(BaseModel):
    """Metadata for a registered dataset."""

    id: str = Field(description="Unique dataset identifier.")
    name: str = Field(description="Human-readable dataset name.")
    row_count: int = Field(description="Number of rows/examples in the dataset.")
    format: str = Field(description="File format (json or jsonl).")
    module_slug: str | None = Field(default=None, description="Associated module slug, when provided.")
    created_at: str = Field(description="ISO-8601 creation timestamp.")


class DatasetListResponse(BaseModel):
    """Paginated dataset listing."""

    items: list[DatasetResponse] = Field(description="Dataset list items.")
    total: int = Field(description="Total matching datasets.")
    offset: int = Field(description="Current pagination offset.")
    limit: int = Field(description="Current page size.")
    has_more: bool = Field(description="Whether more results exist beyond this page.")


class DatasetDetailResponse(DatasetResponse):
    """Dataset metadata with sample rows and URI."""

    sample_rows: list[dict[str, Any]] = Field(description="First rows from the dataset as preview.")
    uri: str = Field(description="Filesystem path to the dataset file.")


class EvaluationResultItem(BaseModel):
    """A single per-example evaluation result."""

    id: str = Field(description="Unique evaluation result identifier.")
    example_index: int = Field(description="Zero-based index in the dataset.")
    input_data: str = Field(description="JSON-serialized input fields.")
    expected_output: str | None = Field(default=None, description="Expected/gold output.")
    predicted_output: str | None = Field(default=None, description="Model predicted output.")
    score: float = Field(description="Score for this example (0.0-1.0).")


class EvaluationResultsResponse(BaseModel):
    """Paginated evaluation results for a run."""

    items: list[EvaluationResultItem] = Field(description="Evaluation result items.")
    total: int = Field(description="Total evaluation results for the run.")
    offset: int = Field(description="Current pagination offset.")
    limit: int = Field(description="Current page size.")
    has_more: bool = Field(description="Whether more results exist beyond this page.")


class PromptSnapshotItem(BaseModel):
    """A before or after prompt snapshot for a predictor."""

    predictor_name: str = Field(description="Predictor name from named_predictors().")
    prompt_type: str = Field(description="Snapshot type: 'before' or 'after'.")
    prompt_text: str = Field(description="Full prompt/instruction text.")


class RunComparisonItem(BaseModel):
    """Summary of a single run for cross-run comparison."""

    run_id: str = Field(description="Optimization run identifier.")
    program_spec: str = Field(description="DSPy program specification optimized.")
    validation_score: float | None = Field(default=None, description="Validation score from the run.")
    prompt_snapshots: list[PromptSnapshotItem] = Field(description="Before/after prompt snapshots for this run.")


class RunComparisonResponse(BaseModel):
    """Cross-run comparison payload."""

    runs: list[RunComparisonItem] = Field(description="Compared run summaries.")
