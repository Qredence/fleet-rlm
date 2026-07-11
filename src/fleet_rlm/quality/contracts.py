"""Typed contracts for the offline GEPA quality lane."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OptimizationTargetRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["module", "skill"]
    target_id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class ModelProfileRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    wire_format: Literal["openai_responses", "openai_chat_completion", "anthropic_messages"]


class _BudgetBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    wall_clock_seconds: int = Field(default=3600, ge=1, le=86_400)


class AutoBudget(_BudgetBase):
    kind: Literal["auto"] = "auto"
    value: Literal["light", "medium", "heavy"]


class MetricCallsBudget(_BudgetBase):
    kind: Literal["max_metric_calls"] = "max_metric_calls"
    value: int = Field(ge=1)


class FullEvalsBudget(_BudgetBase):
    kind: Literal["max_full_evals"] = "max_full_evals"
    value: int = Field(ge=1)


OptimizationBudget = Annotated[
    AutoBudget | MetricCallsBudget | FullEvalsBudget,
    Field(discriminator="kind"),
]


class OptimizationSearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reflection_minibatch_size: int = Field(default=3, ge=1, le=50)
    candidate_selection_strategy: Literal["pareto", "current_best"] = "pareto"
    component_selector: Literal["round_robin", "all"] = "round_robin"
    skip_perfect_score: bool = True
    add_format_failure_as_feedback: bool = False
    use_merge: bool = True
    max_merge_invocations: int | None = Field(default=5, ge=0, le=100)
    seed: int = 0

    @model_validator(mode="after")
    def validate_merge_configuration(self) -> OptimizationSearchConfig:
        if not self.use_merge and self.max_merge_invocations not in (None, 0):
            raise ValueError("max_merge_invocations must be 0 or null when merge is disabled")
        return self


class OptimizationTrackingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    restricted_payloads: bool = False


class OptimizationRunSpec(BaseModel):
    """Immutable, fully resolved identity for one GEPA run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal["phase8-v1"] = "phase8-v1"
    target: OptimizationTargetRef
    dataset_version_id: str = Field(min_length=1)
    metric_profile_id: str = Field(min_length=1)
    task_model: ModelProfileRef
    reflection_model: ModelProfileRef
    budget: OptimizationBudget
    search: OptimizationSearchConfig = Field(default_factory=OptimizationSearchConfig)
    tracking: OptimizationTrackingConfig = Field(default_factory=OptimizationTrackingConfig)
    adapter: Literal["chat", "json", "none"] = "chat"
    dspy_version: str = "3.3.0b1"
    gepa_version: str = "0.1.1"


__all__ = [
    "AutoBudget",
    "FullEvalsBudget",
    "MetricCallsBudget",
    "ModelProfileRef",
    "OptimizationBudget",
    "OptimizationRunSpec",
    "OptimizationSearchConfig",
    "OptimizationTargetRef",
    "OptimizationTrackingConfig",
]
