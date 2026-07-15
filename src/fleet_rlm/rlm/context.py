"""Turn context assembled by TurnCoordinator and consumed by RLMRunner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

from fleet_rlm.artifacts.models import ArtifactCandidate
from fleet_rlm.files.models import PreparedAttachment
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.sessions.models import TurnAccess

AsyncCancellationProbe = Callable[[], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class RLMHistoryMessage:
    """One validated conversational message adapted from committed Session state."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class PreparationNotice:
    """Safe, bounded preparation degradation visible after the stream starts."""

    code: Literal["skills_unavailable"]
    message: str


class RLMInterpreter(Protocol):
    """Narrow interpreter surface consumed by DSPy's RLM adapter."""

    def execute(self, code: str) -> Any: ...


class PreparedCapabilities(Protocol):
    """Already authorized and composed host capabilities for one Run."""

    @property
    def blueprint(self) -> Any: ...

    def drain_public_details(self) -> tuple[Any, ...]: ...

    def drain_artifact_candidates(self) -> tuple[ArtifactCandidate, ...]: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RLMExecutionContext:
    """Complete immutable input accepted by `RLMRunner`."""

    run_id: UUID
    session_id: UUID
    access: TurnAccess
    request: str
    history: tuple[RLMHistoryMessage, ...]
    models: RLMModelBundle
    options: RLMOptions
    deadline: float
    interpreter: RLMInterpreter | None
    attachments: tuple[PreparedAttachment, ...]
    capabilities: PreparedCapabilities
    cancellation_requested: AsyncCancellationProbe
    preparation_notices: tuple[PreparationNotice, ...]
