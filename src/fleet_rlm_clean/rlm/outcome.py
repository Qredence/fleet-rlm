"""Internal turn outcome returned by RLMRunner (not a public RuntimeEvent)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from fleet_rlm_clean.artifacts.models import ArtifactCandidate

TerminalStatus = Literal[
    "completed",
    "cancelled",
    "timeout",
    "budget_exhausted",
    "failed",
]


@dataclass(frozen=True, slots=True)
class TurnExecutionOutcome:
    """Runner result after non-terminal events; coordinator owns public terminals."""

    terminal_status: TerminalStatus
    assistant_text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    artifact_candidates: tuple[ArtifactCandidate, ...] = ()
    public_error_message: str | None = None
    duration_ms: int | None = None

    @property
    def succeeded(self) -> bool:
        return self.terminal_status == "completed"
