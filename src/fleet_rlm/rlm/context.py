"""Run context assembled by TurnCoordinator and consumed by RLMRunner."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Protocol
from uuid import UUID

import dspy

from fleet_rlm.artifacts.models import ArtifactCandidate
from fleet_rlm.chat.run_authority import RunAuthority
from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.files.memory_candidates import MemoryCandidate
from fleet_rlm.files.models import PreparedAttachment
from fleet_rlm.files.workspace_models import UNAVAILABLE_WORKSPACE_CAPABILITY, WorkspaceCapabilityMetadata
from fleet_rlm.rlm.child_runtime import ChildRuntimeFactory
from fleet_rlm.rlm.delegation_metrics import DelegationMetrics
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.dspy_interpreter_contract import CodeInterpreter
from fleet_rlm.rlm.inputs import AttachmentContextCapsule
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.recursive_calls import RecursiveRLMOptions
from fleet_rlm.rlm.signature import FleetRLMSignature
from fleet_rlm.rlm.tool_observer import ToolEventView
from fleet_rlm.sessions.history_transport import CommittedSessionHistory
from fleet_rlm.sessions.models import TurnAccess
from fleet_rlm.skills.models import SkillCard

AsyncCancellationProbe = Callable[[], Awaitable[bool]]


@dataclass(slots=True)
class RetainableEnvironmentRelease:
    """Make one prepared environment release transferable to Session state.

    ``PreparedRun.aclose`` calls :meth:`release`, which is a no-op after the
    Runner transfers ownership.  The resident registry later calls
    :meth:`aclose` and forces the provider release exactly once.
    """

    callback: Callable[[], Awaitable[Any]]
    retained: bool = False
    released: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _release_task: asyncio.Task[Any] | None = field(default=None, init=False, repr=False)

    def retain(self) -> None:
        """Transfer provider ownership from the current Turn to the resident."""
        if self.released:
            raise RuntimeError("environment release is already complete")
        self.retained = True

    async def release(self) -> None:
        """Release from prepared cleanup unless resident ownership was retained."""
        if self.retained:
            return
        await self._release_once()

    async def aclose(self) -> None:
        """Force provider release when the resident runtime is closed."""
        await self._release_once()

    async def _release_once(self) -> None:
        async with self._lock:
            if self.released:
                return
            task = self._release_task
            if task is None:
                task = asyncio.create_task(self._perform_release(), name="fleet-environment-release")
                self._release_task = task
        await asyncio.shield(task)

    async def _perform_release(self) -> None:
        """Run provider release once and publish completion only after success."""
        current = asyncio.current_task()
        try:
            await self.callback()
        except BaseException:
            async with self._lock:
                if self._release_task is current:
                    self._release_task = None
            raise
        async with self._lock:
            if self._release_task is current:
                self.released = True
                self._release_task = None


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

    def drain_memory_candidates(self) -> tuple[MemoryCandidate, ...]: ...

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
    # Canonical committed Session conversation materialized from the
    # claimed checkpoint. Defaults to an empty ``dspy.History`` so
    # ``dspy.RLM._validate_inputs`` always sees a real instance for the
    # Signature-declared ``history`` input. The production Turn-input
    # assembly path (``chat.run_preparation.build_dspy_history_for_claim``)
    # overrides this default with the checkpoint materialization.
    history: dspy.History | CommittedSessionHistory = field(default_factory=lambda: dspy.History(messages=[]))


@dataclass(frozen=True, slots=True)
class ExecutionRuntime:
    """Live execution control: models, limits, interpreter, cancellation."""

    models: RLMModelBundle
    options: RLMOptions
    interpreter: RLMInterpreter | None
    cancellation_requested: AsyncCancellationProbe
    deadline: float
    environment_release: RetainableEnvironmentRelease | None = None


@dataclass(frozen=True, slots=True)
class DelegationPolicy:
    """Cross-sandbox recursive-RLM policy (empty when recursion is disabled)."""

    child_runtime_factory: ChildRuntimeFactory | None = None
    recursive_options: RecursiveRLMOptions = field(default_factory=RecursiveRLMOptions)
    metrics: DelegationMetrics = field(default_factory=DelegationMetrics)


@dataclass(frozen=True, slots=True)
class RLMExecutionContext:
    """Complete immutable input accepted by `RLMRunner`, in five deep members."""

    identity: RunIdentity
    session: SessionView
    execution: ExecutionRuntime
    capabilities: PreparedCapabilities
    delegation: DelegationPolicy = field(default_factory=DelegationPolicy)
    selected_skill_count: int = 0
