"""Run context assembled by TurnCoordinator and consumed by RLMRunner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Protocol
from uuid import UUID

import dspy

from fleet_rlm.artifacts.models import ArtifactCandidate
from fleet_rlm.chat.run_authority import RunAuthority
from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.files.models import PreparedAttachment
from fleet_rlm.files.workspace_models import UNAVAILABLE_WORKSPACE_CAPABILITY, WorkspaceCapabilityMetadata
from fleet_rlm.rlm.child_runtime import ChildRuntimeFactory
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.dspy_interpreter_contract import CodeInterpreter
from fleet_rlm.rlm.inputs import AttachmentContextCapsule
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.recursive_calls import RecursiveRLMOptions
from fleet_rlm.rlm.signature import FleetRLMSignature
from fleet_rlm.rlm.tool_observer import ToolEventView
from fleet_rlm.sessions.models import TurnAccess
from fleet_rlm.skills.models import SkillCard

AsyncCancellationProbe = Callable[[], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class PreparationNotice:
    """Safe, bounded preparation degradation visible after the stream starts."""

    code: Literal["skills_unavailable"]
    message: str


class RLMInterpreter(CodeInterpreter, Protocol):
    """Narrow interpreter surface consumed by DSPy's RLM adapter."""

    def drain_context_accesses(self) -> tuple[str, ...]: ...


class PreparedCapabilities(Protocol):
    """Already authorized and composed host capabilities for one Run."""

    @property
    def spec(self) -> RLMExecutionSpec: ...

    def drain_public_details(self) -> tuple[Any, ...]: ...

    @property
    def preparation_notices(self) -> tuple[PreparationNotice, ...]: ...

    def drain_artifact_candidates(self) -> tuple[ArtifactCandidate, ...]: ...

    def drain_memory_candidates(self) -> tuple[Any, ...]: ...

    def promote_memory_candidates(self, candidates: tuple[Any, ...]) -> Any: ...

    def record_attachment_accesses(self, attachment_ids: tuple[str, ...]) -> None: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RLMExecutionSpec:
    """Host-composed execution inputs independent of Skill extension machinery."""

    skill_cards: tuple[SkillCard, ...] = ()
    signature: type[dspy.Signature] = FleetRLMSignature
    skill_instructions: tuple[str, ...] = ()
    output_schema_id: str = "fleet.default"
    output_schema_version: str = "1"
    tools: tuple[dspy.Tool, ...] = ()
    tool_event_views: Mapping[str, ToolEventView] = field(default_factory=dict)
    workspace: WorkspaceCapabilityMetadata = UNAVAILABLE_WORKSPACE_CAPABILITY

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_event_views", MappingProxyType(dict(self.tool_event_views)))


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """Who/which: the Run's durable identity and authority."""

    run_id: UUID
    session_id: UUID
    access: TurnAccess
    authority: RunAuthority = field(default_factory=RunAuthority)


@dataclass(frozen=True, slots=True)
class SessionView:
    """What the Turn is about, bounded by Session scope."""

    request: str
    session_context: SessionContextManifest
    attachments: tuple[PreparedAttachment, ...]
    attachment_context: AttachmentContextCapsule | None = None
    preparation_notices: tuple[PreparationNotice, ...] = ()
    workspace_memory_digest: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionRuntime:
    """Live execution control: models, limits, interpreter, cancellation."""

    models: RLMModelBundle
    options: RLMOptions
    interpreter: RLMInterpreter | None
    cancellation_requested: AsyncCancellationProbe
    deadline: float


@dataclass(frozen=True, slots=True)
class DelegationPolicy:
    """Cross-sandbox recursive-RLM policy (empty when recursion is disabled)."""

    child_runtime_factory: ChildRuntimeFactory | None = None
    recursive_options: RecursiveRLMOptions = field(default_factory=RecursiveRLMOptions)


@dataclass(frozen=True, slots=True)
class RLMExecutionContext:
    """Complete immutable input accepted by `RLMRunner`, in five deep members."""

    identity: RunIdentity
    session: SessionView
    execution: ExecutionRuntime
    capabilities: PreparedCapabilities
    delegation: DelegationPolicy = field(default_factory=DelegationPolicy)
    selected_skill_count: int = 0
