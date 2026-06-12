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
    skill_name: str | None = Field(
        default=None,
        description="Bundled or mounted Fleet skill name to optimize as a markdown skill artifact.",
    )
    skill_path: str | None = Field(
        default=None,
        description="Relative path to a SKILL.md-compatible markdown file to optimize.",
    )
    trace_bundle_paths: list[str] = Field(
        default_factory=list,
        description="Optional offline trace bundle paths available to the RLM-GEPA instruction proposer.",
    )
    reflection_profile_id: str | None = Field(
        default=None,
        description="Optional LLM provider profile id for the GEPA proposer/reflection model.",
    )
    reflection_model_id: str | None = Field(
        default=None,
        description="Optional provider-native model id for the GEPA proposer/reflection model.",
    )
    output_path: str | None = Field(
        default=None,
        description="Optional filesystem path to save the optimized program.",
    )
    auto: Literal["light", "medium", "heavy"] = Field(
        default="light",
        description="Optimization intensity level.",
    )
    max_metric_calls: int | None = Field(
        default=None,
        ge=1,
        description="Optional GEPA metric-call budget override for short offline smoke runs.",
    )
    train_ratio: float = Field(
        default=0.8,
        description="Fraction of examples to use for training (remainder used for validation).",
    )
    optimizer: Literal["gepa"] = Field(
        default="gepa",
        description="Optimizer backend to use. GEPA is the only supported optimizer.",
    )

    @model_validator(mode="after")
    def validate_dataset_target(self) -> GEPAOptimizationRequest:
        if bool(self.reflection_profile_id) != bool(self.reflection_model_id):
            raise ValueError("reflection_profile_id and reflection_model_id must be provided together")
        if self.dataset_id is not None or (self.dataset_path or "").strip():
            targets = [
                bool((self.program_spec or "").strip()),
                bool((self.module_slug or "").strip()),
                bool((self.skill_name or "").strip()),
                bool((self.skill_path or "").strip()),
            ]
            if sum(targets) == 0:
                raise ValueError(
                    "One optimization target is required: program_spec, module_slug, skill_name, or skill_path"
                )
            if sum(targets) > 1:
                raise ValueError(
                    "Provide only one optimization target: program_spec, module_slug, skill_name, or skill_path"
                )
            if self.skill_name and self.skill_path:
                raise ValueError("Provide either skill_name or skill_path, not both")
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
    feedback_summary: str | None = Field(
        default=None,
        description="Short summary of validation feedback from the GEPA run.",
    )
    module_slug: str | None = Field(
        default=None,
        description="Module slug used for this optimization run, when server-side dispatch was used.",
    )
    reflection_profile_id: str | None = Field(
        default=None,
        description="LLM provider profile id used for GEPA reflection/proposal, when selected.",
    )
    reflection_model_id: str | None = Field(
        default=None,
        description="Model id used for GEPA reflection/proposal, when selected.",
    )
    distilled_trace_bundle_path: str | None = Field(
        default=None,
        description="Distilled trace bundle used by the RLM-GEPA proposer.",
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
    input_keys: list[str] = Field(
        default_factory=list,
        description="Dataset keys used as DSPy inputs for this optimization target.",
    )
    output_keys: list[str] = Field(
        default_factory=list,
        description="Dataset keys scored as DSPy outputs for this optimization target.",
    )
    runtime_module_name: str | None = Field(
        default=None,
        description="Runtime module registry name when this target adapts a runtime module.",
    )
    signature_class_name: str | None = Field(
        default=None,
        description="DSPy signature class optimized by this target, when available.",
    )
    optimization_target_kind: str = Field(
        default="custom",
        description="Optimization target kind such as custom, runtime-signature, or skill.",
    )
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
    reflection_profile_id: str | None = Field(
        default=None,
        description="LLM provider profile id used for GEPA reflection/proposal, when selected.",
    )
    reflection_model_id: str | None = Field(
        default=None,
        description="Model id used for GEPA reflection/proposal, when selected.",
    )
    raw_trace_export_path: str | None = Field(default=None, description="Full raw trace export path, when present.")
    distilled_trace_bundle_path: str | None = Field(
        default=None,
        description="Distilled GEPA trace evidence bundle path, when present.",
    )
    prompt_snapshot_path: str | None = Field(
        default=None,
        description="Prompt snapshot or diff artifact path, when present.",
    )
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


class OptimizationArtifactRef(BaseModel):
    """A filesystem artifact produced or consumed by an optimization run."""

    label: str = Field(description="Human-readable artifact label.")
    path: str = Field(description="Filesystem path to the artifact.")
    kind: str = Field(description="Artifact kind such as manifest, output, trace_bundle, or promotion_draft.")
    exists: bool = Field(default=False, description="Whether the artifact exists on the local filesystem.")


class OptimizationScoreSummary(BaseModel):
    """Score and split summary for a GEPA run."""

    baseline_score: float | None = Field(default=None, description="Baseline validation score, when available.")
    optimized_score: float | None = Field(default=None, description="Optimized validation score, when available.")
    score_delta: float | None = Field(default=None, description="Optimized minus baseline score, when available.")
    train_examples: int | None = Field(default=None, description="Number of training examples.")
    validation_examples: int | None = Field(default=None, description="Number of validation examples.")
    train_ratio: float | None = Field(default=None, description="Requested train/validation split ratio.")
    split_strategy: str | None = Field(default=None, description="Dataset split strategy recorded in the manifest.")


class OptimizationPromptDiffItem(BaseModel):
    """Before/after prompt text for one optimized predictor or skill artifact."""

    predictor_name: str = Field(description="Predictor or skill component name.")
    before_prompt: str = Field(default="", description="Prompt text before GEPA.")
    after_prompt: str = Field(default="", description="Prompt text selected after GEPA.")
    changed: bool = Field(description="Whether the selected prompt differs semantically from the original text.")


class OptimizationTraceEvidenceItem(BaseModel):
    """Distilled trace evidence used by the GEPA proposer."""

    kind: str = Field(description="Distilled bundle record kind.")
    trace_id: str | None = Field(default=None, description="Supporting MLflow trace id.")
    session_id: str | None = Field(default=None, description="MLflow/runtime session id.")
    client_request_id: str | None = Field(default=None, description="Client request id, when available.")
    trace_count: int | None = Field(default=None, description="Trace count for summary records.")
    span_count: int | None = Field(default=None, description="Number of spans in the supporting trace.")
    failure_categories: list[str] = Field(default_factory=list, description="Distilled failure categories.")
    prompt_change_recommendations: list[str] = Field(
        default_factory=list,
        description="Prompt-change recommendations distilled from trace evidence.",
    )


class OptimizationCandidateDecision(BaseModel):
    """A selected or rejected GEPA prompt candidate decision."""

    candidate_id: str = Field(description="Stable candidate identifier for display.")
    status: str = Field(description="Candidate status: selected, rejected, unavailable, or failed.")
    summary: str = Field(description="Human-readable decision summary.")
    rationale: str | None = Field(default=None, description="Why this candidate was selected or rejected.")
    score: float | None = Field(default=None, description="Candidate score, when available.")
    score_delta: float | None = Field(default=None, description="Candidate score delta, when available.")
    artifact_path: str | None = Field(default=None, description="Candidate artifact path, when available.")
    missing_candidate_artifact: bool = Field(
        default=False,
        description="Whether the proposer generated ideas but no candidate artifact was persisted.",
    )


class OptimizationRunInsights(BaseModel):
    """Normalized human-readable improvement insights for a GEPA run."""

    selected_outcome: Literal["changed", "unchanged", "failed", "running", "unknown"] = Field(
        description="Outcome of the selected GEPA artifact."
    )
    summary: str = Field(description="Short explanation of what GEPA did for this run.")
    trace_driven_recommendations: list[str] = Field(
        default_factory=list,
        description="Recommendations distilled from trace evidence.",
    )
    next_step: str = Field(description="Recommended next optimization action.")


class OptimizationHoldoutSummary(BaseModel):
    """Typed holdout validation summary from a GEPA review bundle."""

    promotion_ready: bool = Field(
        default=False,
        description="Whether the run has external holdout validation suitable for promotion.",
    )
    external_validation_available: bool = Field(
        default=True,
        description="Whether a true holdout validation split was available.",
    )
    baseline_score: float | None = Field(default=None, description="Baseline validation score.")
    optimized_score: float | None = Field(default=None, description="Optimized validation score.")
    score_delta: float | None = Field(default=None, description="Optimized minus baseline score.")


class OptimizationReviewBundle(BaseModel):
    """Typed subset of the manifest review bundle used by the optimization UI."""

    version: int = Field(default=1, description="Review bundle schema version.")
    holdout: OptimizationHoldoutSummary | None = Field(
        default=None,
        description="Holdout validation summary for promotion readiness.",
    )
    insights: OptimizationRunInsights | None = Field(
        default=None,
        description="Canonical GEPA insights written at manifest time.",
    )


class OptimizationRunDetailResponse(BaseModel):
    """Detailed GEPA run report for RLM improvement auditability."""

    run: OptimizationRunResponse = Field(description="Base optimization run metadata.")
    manifest_available: bool = Field(description="Whether the manifest file was parsed.")
    manifest: dict[str, Any] | None = Field(default=None, description="Parsed optimization manifest, when available.")
    review_bundle: dict[str, Any] | None = Field(
        default=None,
        description="Parsed manifest review bundle, when available.",
    )
    typed_review_bundle: OptimizationReviewBundle | None = Field(
        default=None,
        description="Typed review bundle fields used by the optimization UI.",
    )
    artifact_refs: list[OptimizationArtifactRef] = Field(description="Important run artifact paths.")
    score_summary: OptimizationScoreSummary = Field(description="Score and split details.")
    prompt_diffs: list[OptimizationPromptDiffItem] = Field(description="Full before/after prompt snapshots.")
    trace_evidence: list[OptimizationTraceEvidenceItem] = Field(
        description="Distilled trace evidence records without raw spans."
    )
    candidate_decisions: list[OptimizationCandidateDecision] = Field(
        description="Selected and rejected candidate decisions when available."
    )
    insights: OptimizationRunInsights = Field(description="Normalized improvement report.")
    optimized_artifact_text: str | None = Field(
        default=None,
        description="Text content of the selected optimized artifact when it is safely readable.",
    )
    optimized_artifact_truncated: bool = Field(
        default=False,
        description="Whether optimized_artifact_text was truncated.",
    )


class OptimizationPromotionDraftResponse(BaseModel):
    """A draft promotion artifact for a completed optimization run."""

    ok: bool = Field(default=True, description="Whether the draft was created or loaded.")
    draft_id: str = Field(description="Stable promotion draft identifier.")
    run_id: str = Field(description="Optimization run id.")
    target: str = Field(description="Skill/module target represented by the draft.")
    status: Literal["draft"] = Field(default="draft", description="Draft status.")
    summary: str = Field(description="Human-readable draft summary.")
    optimized_artifact_path: str | None = Field(default=None, description="Optimized artifact path.")
    manifest_path: str | None = Field(default=None, description="Source manifest path.")
    draft_path: str = Field(description="Filesystem path to the draft artifact.")
    created_at: str = Field(description="ISO timestamp when the draft was created.")


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
