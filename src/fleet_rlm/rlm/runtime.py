"""Native DSPy RLM execution runner, worker lifecycle, and execution context.

This module is the P46.4 runtime entry point. It consolidates execution context,
worker thread scheduling, integrity guards, program fingerprinting, and
`RLMRunner` stream execution over the Session RLM registry.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import logging
import math
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping, Sequence
from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field, replace
from functools import partial
from hashlib import sha256
from json import dumps
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Generic, Literal, Protocol, Self, TypeVar, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import dspy
from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.artifacts.models import ArtifactCandidate
from fleet_rlm.attachments.models import PreparedAttachment
from fleet_rlm.chat.run_authority import RunAuthority
from fleet_rlm.config.settings import Settings
from fleet_rlm.observability.diagnostics import normalize_turn_failure
from fleet_rlm.rlm.budget import BudgetDimension, TurnBudget
from fleet_rlm.rlm.compat_3_3_1 import (
    CodeInterpreter,
    bind_native_rlm_observer,
)
from fleet_rlm.rlm.events import (
    PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE,
    AsyncToolBridge,
    AttachmentRead,
    ExecutionTraceAssembler,
    ObservationSession,
    RLMReasoning,
    RunStarted,
    RuntimeEvent,
    RuntimeEventDetail,
    SkillActivated,
    SkillLoaded,
    Status,
    ToolEventView,
    WarningEvent,
    has_reasoning,
    observe_tool,
    reconcile_trajectory,
)
from fleet_rlm.rlm.output_contract import bind_output_contract
from fleet_rlm.rlm.program import (
    AttachmentContextCapsule,
    FleetRLMSignature,
    RLMFactory,
    RLMModelBundle,
    RLMOptions,
    build_lm,
    build_native_rlm,
    build_rlm_input_kwargs,
    resolve_role_api_key,
    root_signature_for_recursion,
    sanitize_base_url,
)
from fleet_rlm.rlm.recursion import (
    ChildRuntimeFactory,
    DelegationMetrics,
    RecursiveRLMExecutor,
    RecursiveRLMOptions,
    build_recursive_session_snapshot,
)
from fleet_rlm.rlm.result import (
    ExecutionDetail,
    PredictionOutputError,
    RLMConfigError,
    RLMOutcome,
    RunCancelledError,
    RunIntegrityFailureError,
    RunTerminalError,
    TerminalStatus,
    empty_rlm_usage,
    normalize_prediction_trajectory,
    observed_usage,
    prediction_result,
    rlm_termination_mode,
    truncate_public_text,
)
from fleet_rlm.rlm.session_runtime import (
    ProgramFingerprint,
    ProgramFingerprintComponents,
    SessionKey,
    SessionRLMRegistry,
    SessionRLMState,
    SessionRuntimeLease,
    SessionToolRegistry,
)
from fleet_rlm.runtime.owned_effect import OwnedEffect
from fleet_rlm.sessions.history_transport import CommittedSessionHistory
from fleet_rlm.sessions.models import TurnAccess
from fleet_rlm.skills.models import SkillCard
from fleet_rlm.workspace.memory import MemoryCandidate
from fleet_rlm.workspace.models import UNAVAILABLE_WORKSPACE_CAPABILITY, WorkspaceCapabilityMetadata

if TYPE_CHECKING:
    from fleet_rlm.chat.session_context import SessionContextManifest

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Execution Context & Specification
# ---------------------------------------------------------------------------

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
    taint_callback: Callable[[], None] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _release_task: asyncio.Task[Any] | None = field(default=None, init=False, repr=False)

    def retain(self) -> None:
        """Transfer provider ownership from the current Turn to the resident."""
        if self.released:
            raise RuntimeError("environment release is already complete")
        self.retained = True

    def mark_tainted(self) -> None:
        """Tell the provider that the retained root must not be reused."""
        if self.taint_callback is not None:
            self.taint_callback()

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
    # assembly path (``fleet_rlm.chat.preparation.build_dspy_history_for_claim``)
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
    # Composition-owned bridge for async host Tools called synchronously by
    # DSPy's worker-side interpreter.
    async_bridge: AsyncToolBridge | None = None
    # Directly constructed test/in-process contexts opt into the reserve via
    # preparation; the public TOML default is applied by the live composition.
    wrap_up_seconds: float = 0.0


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


# ---------------------------------------------------------------------------
# Tool Guards & Workspace Obligations
# ---------------------------------------------------------------------------


def _fingerprint(tool_name: str, arguments: Mapping[str, Any], result: object) -> str:
    """Fingerprint private values without retaining their bodies in the guard."""
    value = dumps(
        {"tool": tool_name, "arguments": arguments, "result": result},
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(value.encode("utf-8")).hexdigest()


_WORKSPACE_PATH_RE = re.compile(
    r"(?<![\w.-])(?:(?:workspace|projects)/)?(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,16}(?![\w.-])"
)

# Host tool name -> stable guard-target namespace. Fingerprints for existing
# ``session_workspace:`` targets are unchanged; ``projects/<slug>/<path>``
# targets join as ``project_workspace:<slug>/<path>``. The delete/edit tools
# (WS-7) track against the same targets.
_WORKSPACE_TOOL_NAMESPACES = {
    "write_workspace_text": "session_workspace",
    "append_workspace_text": "session_workspace",
    "read_workspace_text": "session_workspace",
    "delete_workspace_path": "session_workspace",
    "edit_workspace_text": "session_workspace",
    "write_project_text": "project_workspace",
    "read_project_text": "project_workspace",
    "delete_project_path": "project_workspace",
    "edit_project_text": "project_workspace",
}

_PREFIX_NAMESPACES = (("projects/", "project_workspace"), ("workspace/", "session_workspace"))


def _canonical_target(path: object, *, namespace: str | None = None) -> str | None:
    """Canonicalize one guard target; an explicit tool namespace is authoritative.

    Without ``namespace`` (request-text obligations) the guard-target language
    infers the namespace from a ``projects/`` or ``workspace/`` path prefix.
    With ``namespace`` (tool-derived targets) prefixes never cross namespaces:
    project tools tolerate only a redundant leading ``projects/`` segment
    (mirroring ``normalize_project_path``), and session-workspace tools use
    their paths verbatim, so a ``projects/...`` path passed to a session tool
    stays a ``session_workspace:`` target.
    """
    if not isinstance(path, str):
        return None
    try:
        from fleet_rlm.workspace.paths import WorkspacePathError, normalize_workspace_path

        normalized = normalize_workspace_path(path)
    except (TypeError, WorkspacePathError):
        return None
    if namespace is None:
        namespace = "session_workspace"
        for prefix, prefix_namespace in _PREFIX_NAMESPACES:
            if normalized.startswith(prefix):
                namespace = prefix_namespace
                normalized = normalized.removeprefix(prefix)
                break
    elif namespace == "project_workspace":
        normalized = normalized.removeprefix("projects/")
    return f"{namespace}:{normalized}"


def _workspace_target(tool_name: str, arguments: Mapping[str, Any]) -> str | None:
    namespace = _WORKSPACE_TOOL_NAMESPACES.get(tool_name)
    if namespace is None:
        return None
    return _canonical_target(arguments.get("path"), namespace=namespace)


def workspace_obligations(request: str) -> frozenset[str] | None:
    """Extract explicit workspace and project file targets from the user's task text."""
    targets: set[str] = set()
    for match in _WORKSPACE_PATH_RE.finditer(request):
        target = _canonical_target(match.group(0))
        if target:
            targets.add(target)
    return frozenset(targets) if targets else None


@dataclass(slots=True)
class RunIntegrityLedger:
    """Keep failed required workspace mutations unresolved until repaired in-place."""

    _unresolved: set[str] = field(default_factory=set)
    required_targets: frozenset[str] | None = None
    _expected_content: dict[str, str] = field(default_factory=dict)

    def _target(self, tool_name: str, arguments: Mapping[str, Any]) -> str | None:
        target = _workspace_target(tool_name, arguments)
        if target is None:
            return None
        if self.required_targets is not None and target not in self.required_targets:
            return None
        return target

    def failed(self, tool_name: str, arguments: Mapping[str, Any]) -> None:
        if target := self._target(tool_name, arguments):
            self._unresolved.add(target)

    def completed(self, tool_name: str, arguments: Mapping[str, Any], result: object) -> None:
        target = self._target(tool_name, arguments)
        if target is None:
            return
        if tool_name in {"write_workspace_text", "write_project_text"}:
            content = arguments.get("content")
            if isinstance(content, str) and target in self._unresolved:
                self._expected_content[target] = sha256(content.encode("utf-8")).hexdigest()
            return
        if tool_name == "append_workspace_text":
            self._unresolved.discard(target)
            self._expected_content.pop(target, None)
            return
        if tool_name in {
            "delete_workspace_path",
            "delete_project_path",
            "edit_workspace_text",
            "edit_project_text",
        }:
            # A successful delete/edit settles the obligation: the mutation is
            # atomic and immediately durable, and its receipt is the completion
            # (a deleted path cannot be read back; an edit's full content is
            # not derivable from its old/new fragments).
            self._unresolved.discard(target)
            self._expected_content.pop(target, None)
            return
        if tool_name in {"read_workspace_text", "read_project_text"}:
            content: object = result
            eof = True
            if isinstance(result, Mapping):
                content = result.get("content")
                eof = result.get("eof") is not False
            expected = self._expected_content.get(target)
            if eof and isinstance(content, str) and expected == sha256(content.encode("utf-8")).hexdigest():
                self._unresolved.discard(target)
                self._expected_content.pop(target, None)

    @property
    def unresolved(self) -> tuple[str, ...]:
        return tuple(sorted(self._unresolved))


@dataclass(slots=True)
class ToolProgressGuard:
    """Emit one bounded warning for identical consecutive host-tool calls."""

    _previous: str | None = None
    _repetitions: int = 0

    def completed(self, tool_name: str, arguments: Mapping[str, Any], result: object) -> str | None:
        fingerprint = _fingerprint(tool_name, arguments, result)
        if fingerprint == self._previous:
            self._repetitions += 1
        else:
            self._previous = fingerprint
            self._repetitions = 0
        if self._repetitions == 1:
            return "repeated tool call produced no progress"
        return None


@dataclass(slots=True)
class RunToolGuards:
    """Small runner-facing interface consolidating mutable per-Run safeguards."""

    integrity: RunIntegrityLedger = field(default_factory=RunIntegrityLedger)
    progress: ToolProgressGuard = field(default_factory=ToolProgressGuard)
    required_targets: frozenset[str] | None = None
    budget: TurnBudget | None = None

    def __post_init__(self) -> None:
        if self.required_targets is not None:
            self.integrity.required_targets = self.required_targets

    def completed(self, tool_name: str, arguments: Mapping[str, Any], result: object) -> str | None:
        self.integrity.completed(tool_name, arguments, result)
        return self.progress.completed(tool_name, arguments, result)

    def failed(self, tool_name: str, arguments: Mapping[str, Any]) -> None:
        """Record that a tool operation failed without resolving its workspace obligation."""
        self.integrity.failed(tool_name, arguments)

    def reserve_tool(self) -> None:
        """Reserve capacity for one tool call in the turn budget."""
        if self.budget is not None:
            self.budget.reserve(BudgetDimension.TOOL_CALLS)


# ---------------------------------------------------------------------------
# Worker Thread Execution & Lifecycle Ownership
# ---------------------------------------------------------------------------

RLMWorkerExecution = Callable[[Any, RLMExecutionContext, Mapping[str, Any]], Coroutine[Any, Any, T]]
_WORKER_SETTLE_EXCEPTIONS = (Exception, asyncio.CancelledError, KeyboardInterrupt, SystemExit)


class WorkerOwnership:
    """Keep one started worker and its blocking resource waiters owned."""

    def __init__(self) -> None:
        """Initialize an empty worker ownership registry."""
        self._effect: OwnedEffect[Any] | None = None
        self._blocking_waiters: list[Callable[[], None]] = []

    def attach(self, effect: OwnedEffect[Any]) -> None:
        """Attach the owned effect without exposing task mechanics."""
        self._effect = effect

    def add_blocking_waiter(self, waiter: Callable[[], None]) -> None:
        """Register synchronous resource ownership that outlives the RLM task."""
        self._blocking_waiters.append(waiter)

    async def wait_owned(self) -> None:
        """
        Wait for the worker and all blocking resource owners to settle.

        Raises:
            BaseException: The first error raised while settling a blocking resource owner.
        """
        if self._effect is not None:
            with contextlib.suppress(BaseException):
                await self._effect.settle()

        # Recursive batch workers run in a separate ThreadPoolExecutor. A
        # Root task can finish after a batch has failed while those workers
        # still own child leases, so wait for each ownership callback off the
        # event loop before Run resources are released.
        waiter_errors: list[BaseException] = []
        for waiter in tuple(self._blocking_waiters):
            owned = OwnedEffect.start(asyncio.to_thread(waiter))
            try:
                await owned.settle()
            except _WORKER_SETTLE_EXCEPTIONS as exc:
                waiter_errors.append(exc)
        if waiter_errors:
            raise waiter_errors[0]


class RLMWorkerHandle(Generic[T]):
    """Typed access to an owned RLM result without exposing task mechanics."""

    def __init__(self, effect: OwnedEffect[T]) -> None:
        """Initialize a worker handle for the owned effect."""
        self._effect = effect

    def done(self) -> bool:
        """Return whether the worker has reached a terminal task state."""
        return self._effect.done()

    def result(self) -> T:
        """Return the worker result, preserving its original exception."""
        return self._effect.result()

    def consume_exception(self) -> None:
        """Mark a completed worker exception as observed without changing its result."""
        self._effect.consume_exception()

    async def wait_until_done(self) -> None:
        """Wait for completion as an observation signal without raising its error."""
        try:
            await self._effect.observe_completion()
        except _WORKER_SETTLE_EXCEPTIONS:
            self.consume_exception()

    async def settle_after_caller_cancellation(self) -> bool:
        """Settle the owned worker and report whether the waiter was cancelled."""
        try:
            await self._effect.settle()
        except _WORKER_SETTLE_EXCEPTIONS:
            self.consume_exception()
        return self._effect.caller_cancelled


async def invoke_native_rlm(
    rlm: Any,
    context: RLMExecutionContext,
    kwargs: Mapping[str, Any],
) -> Any:
    """
    Invoke the RLM operation using the caller-owned interpreter when required.

    Parameters:
        rlm (Any): RLM object to invoke.
        context (RLMExecutionContext): Execution context containing the caller-owned interpreter.
        kwargs (Mapping[str, Any]): Keyword arguments passed to the RLM operation.

    Returns:
        Any: Result produced by the RLM operation.

    Raises:
        RLMConfigError: If an exact native `dspy.RLM` instance is invoked without a caller-owned interpreter.
    """
    native_call_args: tuple[Any, ...] = ()
    if type(rlm) is dspy.RLM:
        if context.execution.interpreter is None:
            raise RLMConfigError("native RLM execution requires a caller-owned interpreter")
        native_call_args = (context.execution.interpreter,)
    return await rlm.acall(*native_call_args, **dict(kwargs))


def start_rlm_worker(
    *,
    rlm: Any,
    context: RLMExecutionContext,
    kwargs: Mapping[str, Any],
    ownership: WorkerOwnership,
    execute: RLMWorkerExecution[T],
    executor: Executor | None = None,
) -> RLMWorkerHandle[T]:
    """Start one non-cancellable RLM worker on a private event loop."""
    effect = OwnedEffect.start(_run_in_worker(rlm, context, kwargs, execute, executor=executor))
    ownership.attach(effect)
    return RLMWorkerHandle(effect)


async def _run_in_worker(
    rlm: Any,
    context: RLMExecutionContext,
    kwargs: Mapping[str, Any],
    execute: RLMWorkerExecution[T],
    *,
    executor: Executor | None = None,
) -> T:
    """
    Execute the RLM operation in a worker thread.

    Returns:
        The result produced by the RLM operation.
    """
    call = partial(_run_private_event_loop, rlm, context, kwargs, execute)
    if executor is None:
        return await asyncio.to_thread(call)
    loop = asyncio.get_running_loop()
    # ``asyncio.to_thread`` propagates ContextVars, while the explicit
    # per-Session executor does not.  Preserve the active Turn trace and
    # callback context across the same worker-affinity path.
    execution_context = contextvars.copy_context()
    return await loop.run_in_executor(executor, execution_context.run, call)


def _run_private_event_loop(
    rlm: Any,
    context: RLMExecutionContext,
    kwargs: Mapping[str, Any],
    execute: RLMWorkerExecution[T],
) -> T:
    """Create and close the worker's event loop around one execution."""
    return asyncio.run(execute(rlm, context, kwargs))


# ---------------------------------------------------------------------------
# RLM Runner & Fingerprint Resolution
# ---------------------------------------------------------------------------


class RLMFactoryLike(Protocol):
    def create(
        self,
        *,
        models: Any,
        options: Any,
        tools: Sequence[dspy.Tool] | None = None,
        signature: Any = None,
        verbose: bool = True,
    ) -> Any:
        """Construct an RLM with the specified models, options, tools, and signature."""
        ...


def _type_shape(value: object) -> str:
    """Return a stable type label without retaining a runtime object."""
    if isinstance(value, type):
        return f"{value.__module__}.{value.__qualname__}"
    origin = getattr(value, "__origin__", None)
    if isinstance(origin, type):
        args = getattr(value, "__args__", ())
        suffix = f"[{','.join(_type_shape(arg) for arg in args)}]" if args else ""
        return f"{origin.__module__}.{origin.__qualname__}{suffix}"
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if isinstance(module, str) and isinstance(qualname, str):
        return f"{module}.{qualname}"
    return type(value).__name__


_FINGERPRINT_SECRET_TEXT_RE = re.compile(
    r"(?i)(?:secret|token|password|credential|authorization|bearer|"
    r"api[ _-]?key|access[ _-]?key|private[ _-]?key)\s*[:=]\s*[^,;\s]+"
)


def _fingerprint_text(value: object) -> object:
    """Keep text shape while dropping content that may contain credentials."""
    if not isinstance(value, str):
        return {"__type__": type(value).__name__}
    # Exact text is not a safe fingerprint input: a Tool description or
    # Signature instruction can contain an unlabeled credential.  Labeled
    # credentials receive one stable marker; otherwise retain only a bounded
    # length shape so public text remains a coarse compatibility signal.
    if _FINGERPRINT_SECRET_TEXT_RE.search(value):
        return {"__redacted_text__": True}
    return {"__text_length__": len(value)}


def _fingerprint_default(value: object) -> object:
    """Return a bounded type/size shape without retaining default values."""
    if value is None:
        return {"__type__": "null"}
    if isinstance(value, bool):
        return {"__type__": "bool"}
    if isinstance(value, int):
        return {"__type__": "int"}
    if isinstance(value, float):
        return {"__type__": "float" if math.isfinite(value) else "nonfinite-float"}
    if isinstance(value, str):
        return _fingerprint_text(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return {"__sequence_type__": _type_shape(type(value)), "__sequence_length__": len(value)}
    if isinstance(value, Mapping):
        return {"__mapping_length__": len(value)}
    return {"__type__": _type_shape(type(value))}


def _field_shape(field: object) -> dict[str, object]:
    """Project a DSPy/Pydantic field to compatibility-only metadata."""
    required_value = getattr(field, "is_required", None)
    if callable(required_value):
        try:
            required = bool(required_value())
        except Exception:
            required = True
    elif isinstance(required_value, bool):
        required = required_value
    else:
        required = True
    extra = getattr(field, "json_schema_extra", None)
    description = getattr(field, "description", None)
    if isinstance(extra, Mapping) and isinstance(extra.get("desc"), str):
        description = extra["desc"]
    shape: dict[str, object] = {
        "annotation": _type_shape(getattr(field, "annotation", str)),
        "required": required,
        "has_default_factory": getattr(field, "default_factory", None) is not None,
        "description": _fingerprint_text(description),
    }
    if not required and getattr(field, "default_factory", None) is None:
        shape["default"] = _fingerprint_default(getattr(field, "default", None))
    return shape


def _field_shapes(fields: Mapping[str, object]) -> tuple[tuple[str, dict[str, object]], ...]:
    """Retain declared field order because DSPy renders Signature order."""
    return tuple((name, _field_shape(field)) for name, field in fields.items())


def _signature_shape(signature: type[dspy.Signature]) -> dict[str, object]:
    """Project input/output fields and instructions, excluding field values."""
    return {
        "inputs": _field_shapes(signature.input_fields),
        "outputs": _field_shapes(signature.output_fields),
        "instructions": _fingerprint_text(getattr(signature, "instructions", "")),
    }


def _public_endpoint(value: object) -> object:
    """Keep endpoint routing shape while dropping query/fragment credentials."""
    if not isinstance(value, str):
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value.split("?", 1)[0].split("#", 1)[0]
    if not parsed.scheme and not parsed.netloc:
        return value.split("?", 1)[0].split("#", 1)[0]
    try:
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host += f":{parsed.port}"
    except ValueError:
        host = parsed.hostname or ""
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


_LM_COMPATIBILITY_KEYS = frozenset(
    {
        "model",
        "model_type",
        "provider",
        "base_url",
        "api_base",
        "endpoint",
        "temperature",
        "top_p",
        "max_tokens",
        "max_completion_tokens",
        "stop",
        "frequency_penalty",
        "presence_penalty",
        "seed",
        "num_retries",
        "timeout",
        "cache",
        "reasoning_effort",
        "response_format",
        "use_developer_role",
        "finetuning_model",
        "custom_llm_provider",
        "deployment_id",
        "parallel_tool_calls",
    }
)


def _lm_shape(lm: object) -> dict[str, object]:
    """Keep an allow-listed, secret-free model policy shape."""
    raw = getattr(lm, "kwargs", {})
    if not isinstance(raw, Mapping):
        raw = {}
    shape: dict[str, object] = {}
    for raw_key, value in raw.items():
        if not isinstance(raw_key, str):
            continue
        normalized = raw_key.strip().lower().replace("-", "_").replace(" ", "_")
        # Unknown provider kwargs may contain request payloads or credentials;
        # they are intentionally not fingerprint inputs.  Public routing and
        # sampling policy is explicit instead of open-ended.
        if normalized not in _LM_COMPATIBILITY_KEYS:
            continue
        if normalized in {"base_url", "api_base", "endpoint"}:
            shape[raw_key] = _public_endpoint(value)
        elif normalized in {"response_format", "stop"}:
            # Retain only a contract/sequence shape, not arbitrary provider
            # payload or stop text that could contain a credential.
            shape[raw_key] = _fingerprint_default(value)
        else:
            shape[raw_key] = value

    # Some DSPy behavior is exposed as attributes rather than kwargs. Provider
    # instances are represented by their qualified type, never by repr/address.
    direct_attrs = (
        "model",
        "model_type",
        "provider",
        "base_url",
        "api_base",
        "temperature",
        "top_p",
        "max_tokens",
        "timeout",
        "cache",
        "num_retries",
        "finetuning_model",
        "use_developer_role",
    )
    for name in direct_attrs:
        if name in shape:
            continue
        try:
            value = getattr(lm, name)
        except Exception:
            continue
        if value is None or callable(value):
            continue
        if name == "provider":
            value = f"{type(value).__module__}.{type(value).__qualname__}"
        elif name in {"base_url", "api_base"}:
            value = _public_endpoint(value)
        shape[name] = value
    return shape


_TOOL_SCHEMA_KEYS = frozenset(
    {
        "$ref",
        "additionalProperties",
        "anyOf",
        "const",
        "default",
        "description",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "items",
        "maximum",
        "maxItems",
        "maxLength",
        "minimum",
        "minItems",
        "minLength",
        "nullable",
        "oneOf",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
        "uniqueItems",
    }
)


def _shape_only(value: object, *, depth: int = 0) -> object:
    """Retain only the type/size shape of an unknown metadata value."""
    if depth > 6:
        return {"type": _type_shape(type(value))}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "bool"}
    if isinstance(value, int):
        return {"type": "int"}
    if isinstance(value, float):
        return {"type": "float"}
    if isinstance(value, str):
        return {"type": "str"}
    if isinstance(value, Mapping):
        # Unknown schema metadata is shape-only; arbitrary keys may themselves
        # be credential material and are not part of the Tool contract.
        return {"type": "mapping", "length": len(value)}
    if isinstance(value, (list, tuple, set, frozenset)):
        return {"type": _type_shape(type(value)), "length": len(value)}
    return {"type": _type_shape(type(value))}


def _tool_schema_shape(value: object, *, key: str | None = None, depth: int = 0) -> object:
    """Copy public JSON-schema semantics while shaping unknown leaves."""
    if depth > 8:
        return _shape_only(value, depth=depth)
    if key in {"default", "const", "enum"}:
        # Defaults/consts/enumerations are data values, not schema shape; a
        # credential can appear there even when its containing schema is safe.
        return _shape_only(value, depth=depth)
    if isinstance(value, str):
        if key in {"description", "title", "pattern", "$ref"}:
            return _fingerprint_text(value)
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                continue
            if raw_key not in _TOOL_SCHEMA_KEYS:
                result[raw_key] = _shape_only(raw_value, depth=depth + 1)
            else:
                result[raw_key] = _tool_schema_shape(raw_value, key=raw_key, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_tool_schema_shape(item, key=key, depth=depth + 1) for item in value]
    return _shape_only(value, depth=depth)


def _tool_shape(tool: dspy.Tool) -> dict[str, object]:
    """Project a Tool contract without callable/source-object state."""
    raw_args = tool.args if isinstance(tool.args, Mapping) else {}
    # DSPy stores Tool ``args`` as an argument-name -> JSON-schema mapping.
    # Canonicalize each argument schema separately; passing the outer mapping
    # to the JSON-schema walker would classify arbitrary argument names as
    # unknown metadata and erase their public ``type`` contract.
    args = {str(name): _tool_schema_shape(schema) for name, schema in raw_args.items() if isinstance(name, str)}
    return {
        "name": str(tool.name),
        "description": _fingerprint_text(tool.desc),
        "args": args,
        "arg_types": {name: _type_shape(value) for name, value in (tool.arg_types or {}).items()}
        if isinstance(tool.arg_types, Mapping)
        else {},
        "arg_descriptions": {name: _fingerprint_text(value) for name, value in (tool.arg_desc or {}).items()}
        if isinstance(tool.arg_desc, Mapping)
        else {},
    }


def _context_binding(context: RLMExecutionContext) -> str:
    """Digest the staged attachment manifest as a runtime reset boundary."""
    capsule = context.session.attachment_context
    if capsule is None:
        return "none"
    return hashlib.sha256(capsule.to_sandbox()).hexdigest()


def _program_fingerprint(
    context: RLMExecutionContext,
    spec: RLMExecutionSpec,
    tools: Sequence[dspy.Tool],
) -> ProgramFingerprint:
    """Compute the resident compatibility identity from explicit program shape."""
    options = context.execution.options
    recursive = context.delegation.recursive_options
    interpreter = context.execution.interpreter
    if interpreter is None:
        interpreter_type = "none"
    else:
        interpreter_type = _type_shape(type(interpreter))
        protocol_version = getattr(interpreter, "protocol_version", None)
        if isinstance(protocol_version, str) and protocol_version:
            interpreter_type += f"@{protocol_version}"
    return ProgramFingerprint.from_components(
        ProgramFingerprintComponents(
            dspy_version=str(dspy.__version__),
            signature_fields=_signature_shape(spec.signature),
            signature_instructions=_fingerprint_text(getattr(spec.signature, "instructions", "")),
            root_lm_config=_lm_shape(context.execution.models.root_lm),
            sub_lm_config=_lm_shape(context.execution.models.sub_lm),
            tools=tuple(_tool_shape(tool) for tool in tools),
            recursion_policy={
                "enabled": recursive.enabled,
                "max_calls": recursive.max_calls,
                "max_prompt_chars": recursive.max_prompt_chars,
                "child_max_iters": recursive.child_max_iters,
                "child_max_llm_calls": recursive.child_max_llm_calls,
                "child_max_output_chars": recursive.child_max_output_chars,
                "max_parallel_children": recursive.max_parallel_children,
            },
            limits={
                "max_iters": options.max_iters,
                "max_llm_calls": options.max_llm_calls,
                "max_output_chars": options.max_output_chars,
            },
            output_contract={
                "schema_id": spec.output_schema_id,
                "schema_version": spec.output_schema_version,
                "fields": _field_shapes(spec.signature.output_fields),
            },
            skill_signature={
                "cards": tuple((card.name, card.version) for card in spec.skill_cards),
                "signature": _type_shape(spec.signature),
            },
            skill_instructions=tuple(_fingerprint_text(item) for item in spec.skill_instructions),
            interpreter_protocol_version=interpreter_type,
        )
    )


def program_fingerprint_for_context(
    context: RLMExecutionContext,
    *,
    spec: RLMExecutionSpec | None = None,
    tools: Sequence[dspy.Tool] | None = None,
) -> ProgramFingerprint:
    """Compute the canonical resident-program identity for a prepared context.

    The runner passes its fully observed Tool set (including recursive Tools).
    Preparation may pass the composed base Tools before wrappers are attached;
    both paths use this one description helper and exclude per-Turn values.
    """
    resolved_spec = spec or context.capabilities.spec
    resolved_tools = tuple(context.capabilities.spec.tools if tools is None else tools)
    return _program_fingerprint(context, resolved_spec, resolved_tools)


class RunEventStream:
    """Async observation iterator with its measured outcome after completion."""

    def __init__(
        self,
        agen: AsyncIterator[RuntimeEvent],
        outcome_factory: Callable[[], RLMOutcome],
        ownership: WorkerOwnership,
        runtime_lease: list[SessionRuntimeLease] | None = None,
    ) -> None:
        """Initialize an event stream with its event iterator and owned Session lane."""
        self._agen = agen.__aiter__()
        self._outcome_factory = outcome_factory
        self._outcome: RLMOutcome | None = None
        self._finished = False
        self._ownership = ownership
        self._runtime_lease_holder = runtime_lease if runtime_lease is not None else []
        self._defer_runtime_release = False
        self._runtime_released = False

    @property
    def outcome(self) -> RLMOutcome | None:
        return self._outcome

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> RuntimeEvent:
        try:
            return await self._agen.__anext__()
        except StopAsyncIteration:
            self._finish()
            raise

    def defer_runtime_release(self) -> None:
        """Keep the Session lane through coordinator settlement and prepared cleanup."""
        self._defer_runtime_release = True

    def mark_committed(self) -> None:
        """Mark the resident state reusable after durable commit succeeds."""
        lease = self._runtime_lease()
        if lease is not None:
            lease.mark_committed()

    def mark_tainted(self) -> None:
        """Mark the resident state unusable after an uncertain outcome."""
        lease = self._runtime_lease()
        if lease is not None:
            lease.mark_tainted()

    async def release_runtime(self) -> None:
        """Return the Session lane exactly once after all Turn cleanup owners finish."""
        if self._runtime_released:
            return
        self._runtime_released = True
        lease = self._runtime_lease()
        if lease is not None:
            await lease.release()

    async def aclose(self) -> None:
        if not self._finished:
            close = getattr(self._agen, "aclose", None)
            if close is not None:
                await close()
            self._finish()
        if not self._defer_runtime_release:
            await self.release_runtime()

    async def wait_owned(self) -> None:
        """Wait for a detached non-cancellable worker under process ownership."""
        await self._ownership.wait_owned()

    def _runtime_lease(self) -> SessionRuntimeLease | None:
        return self._runtime_lease_holder[0] if self._runtime_lease_holder else None

    def _finish(self) -> None:
        """Finalize the event stream and create its outcome once."""
        if not self._finished:
            self._finished = True
            self._outcome = self._outcome_factory()


def _terminal_status(exc: BaseException) -> TerminalStatus:
    """Map an exception to the terminal status of a run.

    Parameters:
        exc (BaseException): The exception that ended the run.

    Returns:
        TerminalStatus: The status associated with the exception.
    """
    if isinstance(exc, RunTerminalError):
        return cast(TerminalStatus, exc.status)
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    return "failed"


def _public_failure_message(exc: BaseException) -> str:
    # Read the instance attribute so a parametrized ``RunTerminalError("...")``
    # override is honored. Class-attr defaults (currently all raise sites)
    # fall through the same lookup.
    """Return a sanitized public message for a run failure.

    Parameters:
        exc (BaseException): The exception describing the failure.

    Returns:
        str: A user-facing failure message appropriate for the exception type.
    """
    if isinstance(exc, PredictionOutputError):
        return str(getattr(exc, "public_message", "Turn output is invalid"))
    if isinstance(exc, RunTerminalError):
        return str(getattr(exc, "public_message", "Turn failed"))
    if isinstance(exc, AdapterParseError):
        return "The model produced a response that could not be parsed into the expected fields."
    if normalize_turn_failure(exc).cause_type == "provider_not_found":
        return PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE
    return "Turn failed"


class RLMRunner:
    """Consume only an immutable prepared context and emit no terminal detail."""

    def __init__(
        self,
        *,
        factory: RLMFactoryLike | None = None,
        runtime_registry: SessionRLMRegistry | None = None,
    ) -> None:
        self._factory = factory or RLMFactory()
        self._owns_runtime_registry = runtime_registry is None
        self._runtime_registry = runtime_registry if runtime_registry is not None else SessionRLMRegistry()
        self._session_tool_registries: dict[SessionKey, SessionToolRegistry] = {}
        self._remove_runtime_close_observer: Callable[[], None] | None = self._runtime_registry.add_close_observer(
            self._on_runtime_closed
        )
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._closed = False

    async def aclose(self, *, drain_seconds: float = 30.0) -> None:
        """Detach observers and boundedly release runner-owned state exactly once."""
        async with self._close_lock:
            if self._closed:
                return
            task = self._close_task
            if task is None:
                task = asyncio.create_task(
                    self._aclose_impl(drain_seconds=drain_seconds),
                    name="fleet-rlm-runner-close",
                )
                self._close_task = task
        try:
            await asyncio.shield(task)
        except BaseException:
            # Cancellation of this waiter must not discard the runner's
            # shielded close owner.  Clear the single-flight handle only when
            # the close task itself has settled and can be retried safely.
            if task.done():
                async with self._close_lock:
                    if self._close_task is task:
                        self._close_task = None
            raise

    async def _aclose_impl(self, *, drain_seconds: float) -> None:
        """Own observer detachment and optional private-registry shutdown."""
        errors: list[BaseException] = []
        remove_observer = self._remove_runtime_close_observer
        if remove_observer is not None:
            try:
                remove_observer()
            except BaseException as exc:
                errors.append(exc)
            else:
                self._remove_runtime_close_observer = None
        # No new callbacks should be retained even when registry shutdown
        # reports a deferred-close failure. The registry itself remains the
        # owner of resident state and can be retried by its composition owner.
        self._session_tool_registries.clear()
        if self._owns_runtime_registry:
            try:
                await self._runtime_registry.shutdown(drain_seconds=drain_seconds)
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise errors[0]
        self._closed = True

    def _on_runtime_closed(self, state: SessionRLMState) -> None:
        """Drop per-Session Tool proxies when their resident state is retired."""
        self._session_tool_registries.pop(state.session_key, None)

    def stream(self, context: RLMExecutionContext) -> RunEventStream:
        """
        Create an event stream for an RLM execution.

        Parameters:
                context (RLMExecutionContext): Context describing the execution to run.

        Returns:
                RunEventStream: Stream of execution events with access to the final outcome.
        """
        outcome: list[RLMOutcome] = []
        ownership = WorkerOwnership()
        runtime_lease: list[SessionRuntimeLease] = []
        events = self._generate(context, outcome, ownership, runtime_lease)
        return RunEventStream(
            events,
            # A stream closed before the generator body ever runs leaves the
            # outcome cell empty; synthesize a cancelled outcome (matching the
            # GeneratorExit path in ``_generate``) instead of raising IndexError.
            lambda: (
                outcome[-1]
                if outcome
                else RLMOutcome(
                    terminal_status="cancelled",
                    usage=empty_rlm_usage(),
                    public_error_message="Turn cancelled",
                    duration_ms=0,
                )
            ),
            ownership,
            runtime_lease,
        )

    async def _generate(
        self,
        context: RLMExecutionContext,
        outcome: list[RLMOutcome],
        ownership: WorkerOwnership,
        runtime_lease: list[SessionRuntimeLease],
    ) -> AsyncIterator[RuntimeEvent]:
        """
        Stream runtime events for an RLM execution and record its terminal outcome.

        Parameters:
            context (RLMExecutionContext): Execution context containing capabilities and runtime state.
            outcome (list[RLMOutcome]): Mutable collection receiving the final execution outcome.
            ownership (WorkerOwnership): Worker lifecycle ownership for the execution.

        Yields:
            RuntimeEvent: An event emitted during execution.
        """
        started = time.perf_counter()
        prediction: list[Any] = []
        try:
            async for event in self._run_success(context, outcome, ownership, prediction, started, runtime_lease):
                yield event
        except (GeneratorExit, asyncio.CancelledError):
            duration_ms = int((time.perf_counter() - started) * 1000)
            outcome.append(
                RLMOutcome(
                    terminal_status="cancelled",
                    usage=observed_usage(prediction[-1] if prediction else None, duration_ms=duration_ms),
                    public_error_message="Turn cancelled",
                    duration_ms=duration_ms,
                )
            )
            raise
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            diagnostic = normalize_turn_failure(exc)
            logger.warning(
                "RLM execution failed (%s) cause_type=%s provider_status_category=%s message=%s",
                type(exc).__name__,
                diagnostic.cause_type,
                diagnostic.provider_status_category,
                diagnostic.message,
                exc_info=exc,
            )
            outcome.append(
                RLMOutcome(
                    terminal_status=_terminal_status(exc),
                    usage=observed_usage(prediction[-1] if prediction else None, duration_ms=duration_ms),
                    public_error_message=_public_failure_message(exc),
                    duration_ms=duration_ms,
                )
            )
        finally:
            if outcome and outcome[-1].terminal_status != "completed":
                context.capabilities.drain_memory_candidates()
            if not outcome:
                context.capabilities.drain_memory_candidates()
                outcome.append(RLMOutcome(terminal_status="failed", public_error_message="Turn failed"))

    async def _run_success(
        self,
        context: RLMExecutionContext,
        outcome: list[RLMOutcome],
        ownership: WorkerOwnership,
        prediction: list[Any],
        started: float,
        runtime_lease: list[SessionRuntimeLease],
    ) -> AsyncIterator[RuntimeEvent]:
        """
        Execute the successful run path and publish its runtime events.

        Parameters:
            context (RLMExecutionContext): Execution context for the run.
            outcome (list[RLMOutcome]): Mutable collection receiving the completed outcome.
            ownership (WorkerOwnership): Worker lifecycle ownership manager.
            prediction (list[Any]): Mutable collection receiving the worker prediction.
            started (float): Monotonic start timestamp used to calculate the duration.

        Yields:
            RuntimeEvent: Events emitted during initialization, execution, and prediction reconciliation.

        Raises:
            RunIntegrityFailureError: If the completed execution has unresolved integrity violations.
        """
        observations = ObservationSession(context.identity.run_id, context.identity.session_id)
        async for event in self._initial_events(context, observations):
            yield event
        spec, guards, worker, recursive_executor, lease = await self._start_worker(context, ownership, observations)
        runtime_lease.append(lease)
        if recursive_executor is not None:
            ownership.add_blocking_waiter(recursive_executor.wait_owned)
        async for event in self._worker_events(context, observations, worker):
            yield event
        prediction.append(worker.result())
        if guards.integrity.unresolved:
            raise RunIntegrityFailureError
        async for event in self._prediction_events(context, observations, prediction[-1]):
            yield event
        duration_ms = int((time.perf_counter() - started) * 1000)
        result = prediction_result(
            prediction[-1],
            spec.signature,
            schema_id=spec.output_schema_id,
            schema_version=spec.output_schema_version,
            max_output_chars=context.execution.options.max_output_chars,
        )
        outcome.append(
            RLMOutcome(
                terminal_status="completed",
                prediction=result,
                usage=observed_usage(prediction[-1], duration_ms=duration_ms),
                artifact_candidates=context.capabilities.drain_artifact_candidates(),
                memory_candidates=context.capabilities.drain_memory_candidates(),
                execution_details=tuple(observations.details),
                duration_ms=duration_ms,
            )
        )

    async def _initial_events(
        self,
        context: RLMExecutionContext,
        observations: ObservationSession,
    ) -> AsyncIterator[RuntimeEvent]:
        """
        Emit initial execution status, preparation notices, and capability details.

        Parameters:
            context (RLMExecutionContext): Execution context containing preparation notices and cancellation state.
            observations (ObservationSession): Session used to record emitted runtime events.

        Yields:
            RuntimeEvent: An initial run, status, warning, or capability event.

        Raises:
            RunCancelledError: If cancellation was requested before execution begins.
        """
        yield observations.record_event(RunStarted(delivery="live"))
        yield observations.record_event(Status("execution", "running"))
        for notice in context.session.preparation_notices:
            yield observations.record(WarningEvent(notice.message, notice.code))
        for item in self._drain_capability_details(context):
            yield observations.record(item)
        if await context.execution.cancellation_requested():
            raise RunCancelledError

    async def _start_worker(
        self,
        context: RLMExecutionContext,
        ownership: WorkerOwnership,
        observations: ObservationSession,
    ) -> tuple[
        RLMExecutionSpec,
        RunToolGuards,
        RLMWorkerHandle[Any],
        RecursiveRLMExecutor | None,
        SessionRuntimeLease,
    ]:
        """
        Acquire and prepare the session runtime, bind turn-specific tools and context, and start the RLM worker.

        Parameters:
            context (RLMExecutionContext): Execution identity, session data, capabilities, and runtime configuration.
            ownership (WorkerOwnership): Ownership state for the worker and its blocking resources.
            observations (ObservationSession): Session used to publish worker and capability events.

        Returns:
            tuple: The execution specification, tool guards, worker handle, optional
            recursive executor, and session runtime lease.

        Raises:
            RLMConfigError: If recursive execution is enabled without a child runtime or
            the session tool registry is unavailable.
        """
        spec = context.capabilities.spec
        guards = RunToolGuards(
            required_targets=workspace_obligations(context.session.request),
            budget=getattr(context.execution.models, "budget", None),
        )
        recursive_executor = None
        if context.delegation.recursive_options.enabled:
            if context.delegation.child_runtime_factory is None:
                raise RLMConfigError("recursive child runtime is unavailable")
            recursive_executor = RecursiveRLMExecutor(
                models=context.execution.models,
                options=context.delegation.recursive_options,
                child_runtime_factory=context.delegation.child_runtime_factory,
                deadline=context.execution.deadline,
                metrics=context.delegation.metrics,
                observer=observations.publish,
                is_authorized=lambda: not context.identity.authority.revoked,
                snapshot=build_recursive_session_snapshot(
                    request=context.session.request,
                    history=context.session.history,
                    session_context=context.session.session_context,
                    workspace=spec.workspace,
                    models=context.execution.models,
                    workspace_memory_digest=context.session.workspace_memory_digest,
                ),
            )
        spec = replace(
            spec,
            signature=root_signature_for_recursion(
                spec.signature,
                recursion_enabled=context.delegation.recursive_options.enabled,
                skill_instructions=spec.skill_instructions,
            ),
        )

        def relay_capability_details(_result: Any) -> None:
            """Relay pending capability details to the event stream."""
            for detail in self._drain_capability_details(context):
                observations.publish(detail)

        observed_tools = tuple(
            observe_tool(
                tool,
                observations.publish,
                spec.tool_event_views.get(str(tool.name), ToolEventView.metadata_only()),
                after_result=(relay_capability_details if str(tool.name) == "load_skill" else None),
                is_authorized=lambda: not context.identity.authority.revoked,
                guards=guards,
                async_bridge=getattr(context.execution, "async_bridge", None),
            )
            for tool in spec.tools
        )
        recursive_tools = (
            (recursive_executor.tool, recursive_executor.batched_tool) if recursive_executor is not None else ()
        )
        all_tools = (*observed_tools, *recursive_tools)
        fingerprint = program_fingerprint_for_context(context, spec=spec, tools=all_tools)
        key = SessionKey(
            workspace_id=str(context.identity.access.workspace_id),
            session_id=str(context.identity.session_id),
        )

        def claim_valid() -> bool:
            return not context.identity.authority.revoked

        created_binding: list[object] = []
        observer_cleanup: Callable[[], object] | None = None
        binding: Any | None = None
        binding_attached = False

        async def create_runtime(session_key: SessionKey, program_fingerprint: str) -> SessionRLMState:
            manager = self._session_tool_registries.get(session_key)
            if manager is None:
                manager = SessionToolRegistry()
                self._session_tool_registries[session_key] = manager
            binding = manager.bind_turn(
                all_tools,
                run_id=context.identity.run_id,
                claim_valid=claim_valid,
                authorized_names={str(tool.name) for tool in all_tools},
                revocation=context.identity.authority,
            )
            created_binding.append(binding)
            worker_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix=f"fleet-session-rlm-{session_key.session_id[:8]}",
            )
            environment_release = context.execution.environment_release
            try:
                rlm = self._factory.create(
                    models=context.execution.models,
                    options=context.execution.options,
                    tools=binding.tools or None,
                    signature=spec.signature,
                )
                if environment_release is not None:
                    environment_release.retain()
                return SessionRLMState(
                    session_key=session_key,
                    program_fingerprint=program_fingerprint,
                    rlm=rlm,
                    interpreter=context.execution.interpreter,
                    root_lease=environment_release,
                    cleanup_handle=binding,
                    tool_registry=manager,
                    worker_executor=worker_executor,
                    interpreter_owned_by_root=environment_release is not None,
                )
            except BaseException:
                worker_executor.shutdown(wait=True, cancel_futures=True)
                binding.remove()
                if environment_release is not None:
                    with suppress(BaseException):
                        await environment_release.aclose()
                raise

        lease = await self._runtime_registry.acquire_execution(
            key,
            fingerprint,
            create_runtime,
            context_binding=_context_binding(context),
            # If only the program fingerprint changes, the Daytona provider
            # may intentionally hand the same root-owned interpreter to this
            # Turn.  The registry transfers its root lease before closing the
            # previous generation instead of shutting that interpreter down.
            preserve_interpreter=context.execution.interpreter,
        )
        manager = self._session_tool_registries.get(key)
        if manager is None:
            # Tool registries are resident-program state.  A second Runner
            # sharing the injected Session registry must recover the exact
            # registry attached by the original factory, rather than creating
            # proxies that the resident RLM never references.
            resident_manager = lease.state.tool_registry
            if isinstance(resident_manager, SessionToolRegistry):
                manager = resident_manager
                self._session_tool_registries[key] = manager
        if manager is None:
            # A factory always installs the first binding.  This guard keeps
            # custom registries/factories fail-closed if they violate that
            # resident-state contract.
            await lease.release()
            raise RLMConfigError("Session Tool registry is unavailable")
        try:
            environment_release = context.execution.environment_release
            if environment_release is not None:
                # Each preparation may hand us a fresh per-Turn wrapper around the
                # same provider root. Retain it before prepared cleanup; the
                # provider callback is idempotent and the first resident owner
                # remains responsible for final root release.
                environment_release.retain()
            binding = (
                created_binding.pop()
                if created_binding
                else manager.bind_turn(
                    all_tools,
                    run_id=context.identity.run_id,
                    claim_valid=claim_valid,
                    authorized_names={str(tool.name) for tool in all_tools},
                    revocation=context.identity.authority,
                )
            )
            state_context = replace(
                context,
                execution=replace(context.execution, interpreter=lease.state.interpreter),
            )

            def clear_observers() -> None:
                """Drop run-local callbacks before the resident state goes idle."""
                for target in (state_context.execution.interpreter, lease.state.rlm):
                    with suppress(BaseException):
                        self._clear_observer(target)

            # Register cleanup before the first bind. Context-capsule setup or
            # native callback installation can fail independently; a partial
            # start must not leave a resident interpreter pointing at this
            # Turn's ObservationSession.
            observer_cleanup = clear_observers
            lease.bind_turn_cleanup(binding, observer_cleanup)
            binding_attached = True
            bind_budget = getattr(state_context.execution.interpreter, "bind_turn_budget", None)
            if callable(bind_budget):
                bind_budget(getattr(state_context.execution.models, "budget", None))
            self._bind_observer(
                state_context.execution.interpreter,
                observations.publish,
                state_context.execution.options.max_output_chars,
                deadline=state_context.execution.deadline,
            )
            # The resident native RLM owns its sub-LM reference for the
            # duration of ``_make_llm_tools``. Refresh it for every Turn so a
            # reused Session never retains a prior Turn's deadline-bound copy.
            if hasattr(lease.state.rlm, "sub_lm"):
                lease.state.rlm.sub_lm = state_context.execution.models.sub_lm
            self._bind_context_capsule(state_context)
            bind_output_contract(
                state_context.execution.interpreter,
                getattr(lease.state.rlm, "signature", None),
            )
            self._bind_observer(
                lease.state.rlm,
                observations.publish,
                state_context.execution.options.max_output_chars,
                emit_reasoning=True,
                deadline=state_context.execution.deadline,
            )
            kwargs = build_rlm_input_kwargs(
                request=state_context.session.request,
                session_context=state_context.session.session_context,
                skill_cards=spec.skill_cards,
                attachments=state_context.session.attachments,
                attachment_context=state_context.session.attachment_context,
                workspace=spec.workspace,
                workspace_memory_digest=state_context.session.workspace_memory_digest,
                history=state_context.session.history,
            )
            trace = ExecutionTraceAssembler(recursive_executor)
            worker = start_rlm_worker(
                rlm=lease.state.rlm,
                context=state_context,
                kwargs=kwargs,
                ownership=ownership,
                execute=trace.execute,
                executor=lease.state.worker_executor,
            )
            return spec, guards, worker, recursive_executor, lease
        except BaseException:
            if observer_cleanup is not None:
                with suppress(BaseException):
                    observer_cleanup()
            if binding is not None and not binding_attached:
                remove = getattr(binding, "remove", None)
                if callable(remove):
                    with suppress(BaseException):
                        remove()
            lease.mark_tainted()
            await lease.release()
            raise

    async def _worker_events(
        self,
        context: RLMExecutionContext,
        observations: ObservationSession,
        worker: RLMWorkerHandle[Any],
    ) -> AsyncIterator[RuntimeEvent]:
        """
        Yield worker observations and newly available capability details as runtime events.
        """
        async for event in observations.stream_worker(
            worker,
            context,
            lambda: self._drain_capability_details(context),
        ):
            yield event

    async def _prediction_events(
        self,
        context: RLMExecutionContext,
        observations: ObservationSession,
        prediction: Any,
    ) -> AsyncIterator[RuntimeEvent]:
        """
        Emit reconciled prediction details, final reasoning, and capability events.

        Parameters:
            context (RLMExecutionContext): Execution context containing output limits and capability details.
            observations (ObservationSession): Session used to record emitted runtime events.
            prediction (Any): Prediction whose trajectory and final reasoning are converted into events.
        """
        trajectory = normalize_prediction_trajectory(prediction)
        for item in reconcile_trajectory(
            observations.details, trajectory, max_chars=context.execution.options.max_output_chars
        ):
            # ``reconcile_trajectory`` appends the canonical details to the
            # observation list; emit them without recording them a second time.
            yield observations.record_event(item)
        final_reasoning = getattr(prediction, "final_reasoning", None)
        if isinstance(final_reasoning, str) and final_reasoning.strip():
            public_reasoning = truncate_public_text(final_reasoning, max_len=context.execution.options.max_output_chars)
            if not has_reasoning(observations.details, public_reasoning, context.execution.options.max_output_chars):
                item = RLMReasoning(public_reasoning)
                yield observations.record(item)
        for item in self._drain_capability_details(context):
            yield observations.record(item)

    @staticmethod
    def _bind_context_capsule(context: RLMExecutionContext) -> None:
        """Bind the prepared Volume context to the interpreter before the RLM starts."""
        if context.session.attachment_context is None:
            return
        bind = getattr(context.execution.interpreter, "bind_context_capsule", None)
        if callable(bind):
            bind(context.session.attachment_context)

    @staticmethod
    def _bind_observer(
        target: Any,
        publish: Callable[[RuntimeEventDetail], None],
        max_chars: int,
        *,
        emit_reasoning: bool = True,
        deadline: float | None = None,
    ) -> None:
        """
        Bind an observation callback to a supported RLM target.

        Parameters:
            target (Any): RLM instance or target exposing a ``bind_observer`` method.
            publish (Callable[[RuntimeEventDetail], None]): Callback for publishing runtime event details.
            max_chars (int): Maximum number of characters retained per observed detail.
            emit_reasoning (bool): Whether native RLM reasoning events should be published.
            deadline (float | None): Absolute Turn deadline used to suppress late native spans.
        """
        if type(target) is dspy.RLM:
            bind_native_rlm_observer(
                target,
                publish if emit_reasoning else None,
                max_chars=max_chars,
                deadline=deadline,
            )
            return
        bind = getattr(target, "bind_observer", None)
        if callable(bind):
            bind(publish, max_chars=max_chars)

    @staticmethod
    def _clear_observer(target: Any) -> None:
        """Remove Fleet's run-local observer without touching other callbacks."""
        if type(target) is dspy.RLM:
            bind_native_rlm_observer(target, None)
            return
        bind = getattr(target, "bind_observer", None)
        if callable(bind):
            bind(None)

    @staticmethod
    def _drain_capability_details(context: RLMExecutionContext) -> tuple[ExecutionDetail, ...]:
        """
        Collects and validates the public capability details from an execution context.

        Parameters:
                context (RLMExecutionContext): Execution context containing capability details.

        Returns:
                tuple[ExecutionDetail, ...]: The supported public capability details.

        Raises:
                TypeError: If the capability host returns an unsupported detail type.
        """
        values = context.capabilities.drain_public_details()
        if not all(isinstance(item, (AttachmentRead, SkillActivated, SkillLoaded, WarningEvent)) for item in values):
            raise TypeError("capability host returned an unsupported public detail")
        return values


# ---------------------------------------------------------------------------
# Provider compatibility probe for the pinned native DSPy RLM protocol
# ---------------------------------------------------------------------------

ProbeInterpreterFactory = Callable[[], CodeInterpreter]


class RLMProviderContractError(RLMConfigError):
    """The configured Root LM cannot satisfy Fleet's native RLM action contract."""


@dataclass(frozen=True, slots=True)
class RLMProviderProbeResult:
    """Bounded readiness evidence; provider payloads are never retained."""

    iterations: int
    termination_mode: str


class _ProviderProbeSignature(dspy.Signature):
    """Select a bounded value, delegate it to rlm_query, then call typed SUBMIT(answer=...)."""

    probe: str = dspy.InputField()
    answer: str = dspy.OutputField()


def _root_lm(settings: Settings) -> dspy.LM:
    role = settings.root_lm
    api_key = resolve_role_api_key(settings, role)
    if not api_key:
        raise RLMProviderContractError(f"Root LM API key is not configured ({role.api_key_env})")
    return build_lm(
        role.model,
        api_key=api_key,
        base_url=sanitize_base_url(role.base_url),
        max_tokens=role.max_tokens,
        timeout_seconds=role.timeout_seconds,
        temperature=role.temperature,
        reasoning_effort=role.reasoning_effort,
        cache=False,
        num_retries=role.num_retries,
    )


async def probe_root_lm(
    root_lm: Any,
    *,
    interpreter_factory: ProbeInterpreterFactory,
    child_runtime_factory: ChildRuntimeFactory,
) -> RLMProviderProbeResult:
    """
    Probe a root language model for compatibility with the native recursive RLM protocol.

    Parameters:
        root_lm (Any): The root language model to test.
        interpreter_factory (ProbeInterpreterFactory): Factory for a fresh provider-neutral interpreter.
        child_runtime_factory (ChildRuntimeFactory): Factory for a fresh provider-neutral child runtime.

    Returns:
        RLMProviderProbeResult: The number of RLM iterations and the termination mode.

    Raises:
        RLMProviderContractError: If the model fails the recursive RLM compatibility
            requirements or produces an invalid response.
    """

    interpreter = interpreter_factory()
    recursive = RecursiveRLMExecutor(
        models=RLMModelBundle(root_lm=root_lm, sub_lm=root_lm),
        options=RecursiveRLMOptions(max_calls=1, max_prompt_chars=2_000),
        child_runtime_factory=child_runtime_factory,
        deadline=time.monotonic() + 120,
    )
    rlm = build_native_rlm(
        signature=_ProviderProbeSignature,
        options=RLMOptions(max_iters=4, max_llm_calls=4, max_output_chars=2_000),
        tools=[recursive.tool],
    )
    try:
        with dspy.context(lm=root_lm, adapter=dspy.JSONAdapter(), track_usage=False):
            prediction = await rlm.acall(
                interpreter,
                probe=(
                    "Set marker = 'probe-slice'. On a later REPL iteration call "
                    "child = rlm_query(prompt='Classify this selected value: ' + marker), "
                    "then submit the child answer with typed SUBMIT(answer=child). "
                    "Use at least three REPL iterations and keep the prompt bounded."
                ),
            )
    except AdapterParseError as exc:
        raise RLMProviderContractError("Root LM returned an unparseable RLM action") from exc
    except Exception as exc:
        raise RLMProviderContractError("Root LM RLM compatibility probe failed") from exc
    finally:
        interpreter.shutdown()

    trajectory = getattr(prediction, "trajectory", ())
    answer = getattr(prediction, "answer", None)
    if not isinstance(trajectory, list) or len(trajectory) < 3:
        raise RLMProviderContractError("Root LM did not complete a multi-step RLM sequence")
    if not isinstance(answer, str) or not answer.strip():
        raise RLMProviderContractError("Root LM did not reach typed SUBMIT output")
    if recursive.summary().call_count < 1:
        raise RLMProviderContractError("Root LM did not exercise the recursive child Tool")
    return RLMProviderProbeResult(
        iterations=len(trajectory),
        termination_mode=rlm_termination_mode(prediction),
    )


async def probe_configured_root_lm(
    settings: Settings,
    *,
    interpreter_factory: ProbeInterpreterFactory,
    child_runtime_factory: ChildRuntimeFactory,
) -> RLMProviderProbeResult:
    """Build only the policy-selected Root LM and probe it once."""

    return await probe_root_lm(
        _root_lm(settings),
        interpreter_factory=interpreter_factory,
        child_runtime_factory=child_runtime_factory,
    )


__all__ = [
    "AsyncCancellationProbe",
    "DelegationPolicy",
    "ExecutionRuntime",
    "PreparationNotice",
    "PreparedCapabilities",
    "ProbeInterpreterFactory",
    "RLMExecutionContext",
    "RLMExecutionSpec",
    "RLMFactoryLike",
    "RLMInterpreter",
    "RLMProviderContractError",
    "RLMProviderProbeResult",
    "RLMRunner",
    "RLMWorkerHandle",
    "RunEventStream",
    "RunIdentity",
    "RunIntegrityLedger",
    "RunToolGuards",
    "SessionView",
    "ToolProgressGuard",
    "WorkerOwnership",
    "probe_configured_root_lm",
    "probe_root_lm",
    "start_rlm_worker",
    "workspace_obligations",
]
