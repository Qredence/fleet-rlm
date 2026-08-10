"""Internal immutable outcome returned by `RLMRunner`."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, TypeAlias

from fleet_rlm.artifacts.models import ArtifactCandidate
from fleet_rlm.files.memory_candidates import MemoryCandidate
from fleet_rlm.rlm.dspy_contract import PredictionResult, RLMUsage, empty_rlm_usage, validate_rlm_usage
from fleet_rlm.rlm.events import (
    AttachmentRead,
    RLMCode,
    RLMOutput,
    RLMReasoning,
    SkillActivated,
    SkillLoaded,
    StepFinished,
    StepStarted,
    ToolCompleted,
    ToolFailed,
    ToolStarted,
    WarningEvent,
)

TerminalStatus: TypeAlias = Literal["completed", "cancelled", "timeout", "failed"]
ExecutionDetail: TypeAlias = (
    StepStarted
    | StepFinished
    | RLMReasoning
    | RLMCode
    | RLMOutput
    | ToolStarted
    | ToolCompleted
    | ToolFailed
    | SkillActivated
    | SkillLoaded
    | AttachmentRead
    | WarningEvent
)


@dataclass(frozen=True, slots=True)
class RLMOutcome:
    """Runner result after non-terminal observations; lifecycle owns settlement."""

    terminal_status: TerminalStatus
    prediction: PredictionResult | None = None
    usage: RLMUsage = field(default_factory=empty_rlm_usage)
    artifact_candidates: tuple[ArtifactCandidate, ...] = ()
    memory_candidates: tuple[MemoryCandidate, ...] = ()
    execution_details: tuple[ExecutionDetail, ...] = ()
    public_error_message: str | None = None
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        usage = validate_rlm_usage(self.usage)
        object.__setattr__(self, "usage", MappingProxyType(usage))
        if self.terminal_status == "completed" and self.public_error_message is not None:
            raise ValueError("a successful outcome cannot contain a public error")
        if (self.terminal_status == "completed") != (self.prediction is not None):
            raise ValueError("only a successful outcome must contain a prediction")

    @property
    def succeeded(self) -> bool:
        return self.terminal_status == "completed"
