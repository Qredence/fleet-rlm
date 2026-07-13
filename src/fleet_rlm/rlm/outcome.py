"""Internal immutable outcome returned by `RLMRunner`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, TypeAlias

from fleet_rlm.artifacts.models import ArtifactCandidate
from fleet_rlm.rlm.events import (
    AttachmentRead,
    JsonValue,
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

TerminalStatus: TypeAlias = Literal["completed", "cancelled", "timeout", "budget_exhausted", "failed"]
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
    text: str = ""
    usage: Mapping[str, JsonValue] = field(default_factory=dict)
    artifact_candidates: tuple[ArtifactCandidate, ...] = ()
    execution_details: tuple[ExecutionDetail, ...] = ()
    structured_output: JsonValue | None = None
    result_schema_id: str | None = None
    result_schema_version: str | None = None
    public_error_message: str | None = None
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "usage", MappingProxyType(dict(self.usage)))
        if self.terminal_status == "completed" and self.public_error_message is not None:
            raise ValueError("a successful outcome cannot contain a public error")
        if (self.result_schema_id is None) != (self.result_schema_version is None):
            raise ValueError("structured result schema id and version must be provided together")

    @property
    def succeeded(self) -> bool:
        return self.terminal_status == "completed"
