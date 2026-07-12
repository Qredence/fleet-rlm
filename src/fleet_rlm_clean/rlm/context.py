"""Turn context assembled by TurnCoordinator and consumed by RLMRunner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from fleet_rlm_clean.rlm.budgets import RLMBudget
from fleet_rlm_clean.rlm.model_bundle import RLMModelBundle


class InterpreterLeaseLike(Protocol):
    """Narrow lease surface used by RLMRunner (release never deletes Sandbox)."""

    interpreter: Any

    def release(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RLMTurnContext:
    """Everything required to execute one recursive turn.

    ``skill_cards`` holds authorized SkillCard-like metadata (no instruction bodies).
    Attachment/artifact refs remain opaque until wired into the runner tools.
    """

    run_id: UUID
    session_id: UUID
    user_id: UUID
    workspace_id: UUID
    request: str
    models: RLMModelBundle
    budget: RLMBudget
    lease: InterpreterLeaseLike
    history: Any = None
    session_summary: str = ""
    skill_cards: tuple[Any, ...] = field(default_factory=tuple)
    attachments: tuple[Any, ...] = field(default_factory=tuple)
    artifacts: tuple[Any, ...] = field(default_factory=tuple)
    tools: tuple[Any, ...] = field(default_factory=tuple)
    # Optional SkillToolHost for progressive load tools + skill.loaded events
    skill_tool_host: Any | None = None
