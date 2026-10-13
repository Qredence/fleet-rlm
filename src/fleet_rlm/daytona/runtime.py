"""Public Daytona runtime boundary for reusable roots and disposable children.

DaytonaRuntime manages session-scoped reusable root Sandboxes and ephemeral
disposable child Environments for recursive execution.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

from fleet_rlm.daytona.children import (
    ChildRuntimeLeaseState,
    ChildSandboxes,
)
from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    ProviderRequestError,
    is_safe_pre_creation_retry,
    is_sandbox_not_found,
    map_daytona_sdk_error,
    map_provider_error,
)
from fleet_rlm.daytona.host_io import WorkspaceIO
from fleet_rlm.daytona.interpreter import (
    DEFAULT_EXECUTION_OUTPUT_CHARS,
    DaytonaCodeInterpreter,
    SyncBridgeDispatcher,
)
from fleet_rlm.daytona.leases import (
    ActiveLeaseConflictError,
    ActiveLeaseRegistry,
    DaytonaAdmission,
    DaytonaAdmissionPermit,
    DaytonaLeaseAcquisitionTimeoutError,
    InterpreterLease,
    LeaseRequest,
    LeaseState,
    SandboxResource,
    _provider_call,
    _ProviderCallDeadlineError,
    _retain_provider_task,
    _settle_provider_task,
    schedule_owned_close,
)
from fleet_rlm.daytona.provider import (
    LiveDaytonaPlatform,
    LiveDaytonaVolumeClient,
    _build_interpreter_factory,
    build_daytona_client,
)
from fleet_rlm.daytona.specs import (
    PREWARM_RUN_ID,
    DaytonaEnvironmentProfile,
    DaytonaSandboxSpec,
    ExpectedWorkspaceMount,
    SandboxPlatform,
    VolumeClient,
    VolumeConfig,
    _create_daytona_sandbox,
    _expected_workspace_mount,
    ensure_directories,
    ensure_execution_layout,
    ensure_volume_layout,
    get_or_create_volume_id,
    is_not_found,
    sandbox_filesystem,
    sandbox_spec_from_settings,
    sandbox_state,
    verify_execution_mount,
    verify_sandbox_spec,
    verify_sandbox_workspace_mount,
    volume_config_from_settings,
)
from fleet_rlm.paths import SESSION_WORKSPACE_MOUNT_PATH
from fleet_rlm.rlm.ownership import OwnedEffect, RunCleanupSupervisor
from fleet_rlm.sessions.bindings import (
    BindingGenerationAuthority,
    SandboxBinding,
    require_non_zero_workspace_id,
    require_scoped_volume_subpath,
    require_session_workspace_subpath,
    session_workspace_volume_subpath,
    workspace_volume_subpath,
)

if TYPE_CHECKING:
    from fleet_rlm.config.settings import Settings


_WORKSPACE_IO_DELETE_GRACE_SECONDS = 15.0
_PREWARM_CLAIM_WAIT_SECONDS = 60.0


__all__ = ["DaytonaRuntime", "DaytonaRuntimeState", "RootSessionSpec"]


_sandbox_filesystem = sandbox_filesystem
_is_not_found = is_not_found
_ensure_directories = ensure_directories


# --- Process-local Daytona Admission ---


# --- Confirmed Sandbox Deletion Lifecycle ---


async def _claim_session_lease(
    registry: ActiveLeaseRegistry, session_id: UUID, run_id: UUID, *, workspace_id: UUID, deadline: float
) -> None:
    loop = asyncio.get_running_loop()
    claim_wait_deadline = loop.time() + _PREWARM_CLAIM_WAIT_SECONDS
    while True:
        try:
            registry.acquire(session_id, run_id, workspace_id=workspace_id)
            return
        except ActiveLeaseConflictError as exc:
            if run_id == PREWARM_RUN_ID or exc.holder_run_id != PREWARM_RUN_ID:
                raise
        remaining = min(deadline, claim_wait_deadline) - loop.time()
        if remaining <= 0:
            raise DaytonaLeaseAcquisitionTimeoutError("Daytona lease acquisition timed out") from None
        await asyncio.sleep(min(0.2, remaining))


DaytonaRuntimeState = LeaseState


def _identity_text(value: UUID | str | None, name: str) -> str:
    if value is None:
        raise ValueError(f"{name} is required")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _coerce_uuid(value: UUID | str, name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    text = _identity_text(value, name)
    try:
        return UUID(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid UUID: {text}") from exc


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


@dataclass(frozen=True, slots=True)
class RootSessionSpec:
    """Immutable identity and context selectors for one reusable root."""

    workspace_id: UUID | str
    session_id: UUID | str
    user_id: UUID | str | None = None
    run_id: UUID | str | None = None
    context_fingerprint: object | None = None
    deadline: float | None = None
    force_new: bool = False

    def __post_init__(self) -> None:
        _identity_text(self.workspace_id, "workspace_id")
        _identity_text(self.session_id, "session_id")
        if self.deadline is not None and not isinstance(self.deadline, (int, float)):
            raise TypeError("deadline must be numeric or None")

    @property
    def key(self) -> tuple[str, str]:
        """Return the stable root registry key."""
        return (_identity_text(self.workspace_id, "workspace_id"), _identity_text(self.session_id, "session_id"))

    @property
    def fingerprint(self) -> object | None:
        """Alias for the context selector used for root reuse."""
        return self.context_fingerprint


@contextlib.asynccontextmanager
async def _admitted(capacity: asyncio.Semaphore, deadline: float | None):
    """Hold one bounded capacity slot only around a provider operation."""
    if deadline is None:
        await capacity.acquire()
    else:
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        await asyncio.wait_for(capacity.acquire(), timeout=remaining)
    try:
        yield
    finally:
        capacity.release()


class SessionCleanupState(StrEnum):
    """Durable cleanup position of one runtime-owned session record."""

    ACTIVE = "ACTIVE"
    RELEASING = "RELEASING"
    RETIRED = "RETIRED"
    UNRESOLVED = "UNRESOLVED"


@dataclass(slots=True)
class DaytonaSessionRecord:
    """Runtime-owned resource record for one reusable session Sandbox.

    This record holds workspace/session identity,
    Sandbox/Volume handles, binding generation, the active invocation, and
    cleanup state. It carries no prompts, tools, or durable turn state.
    """

    workspace_id: str
    session_id: str
    root: InterpreterLease | None = None
    cleanup_state: SessionCleanupState = SessionCleanupState.ACTIVE

    @property
    def key(self) -> tuple[str, str]:
        """Return the stable Session resource key."""
        return (self.workspace_id, self.session_id)


logger = logging.getLogger(__name__)


def _sandbox_id(sandbox: Any) -> str:
    sid = getattr(sandbox, "id", None)
    if sid is None:
        raise DaytonaAdapterError(message="sandbox missing id", cause_type="SandboxIdentityError")
    return str(sid)


def binding_matches_expected(binding: SandboxBinding, expected: ExpectedWorkspaceMount) -> bool:
    try:
        require_non_zero_workspace_id(binding.workspace_id)
        if expected.session_id is None:
            require_scoped_volume_subpath(binding.volume_subpath, workspace_id=binding.workspace_id)
        else:
            require_session_workspace_subpath(
                binding.volume_subpath,
                workspace_id=binding.workspace_id,
                session_id=expected.session_id,
            )
    except (TypeError, ValueError):
        return False
    return (
        binding.workspace_id == expected.workspace_id
        and binding.volume_id == expected.volume_id
        and binding.volume_subpath == expected.volume_subpath
        and binding.mount_path == expected.mount_path
    )


@dataclass(slots=True)
class _LateOwner:
    request: LeaseRequest
    run_id: UUID
    permit: DaytonaAdmissionPermit | None = None
    acquisition: asyncio.Task[InterpreterLease] | None = None
    lease: InterpreterLease | None = None
    cleanup_task: Any | None = None
    cleanup_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    callback_started: bool = False
    callback_settled: bool = False
    unpublished: bool = False


@dataclass(slots=True)
class _AcquisitionContext:
    expected: ExpectedWorkspaceMount
    binding: SandboxBinding | None
    persisted_binding: SandboxBinding | None = None


class BindingStoreLike(Protocol):
    async def get(self, session_id: UUID) -> SandboxBinding | None: ...
    async def upsert(self, binding: SandboxBinding) -> SandboxBinding: ...
    async def get_scoped(self, session_id: UUID, *, workspace_id: UUID) -> SandboxBinding | None: ...
    async def replace_with_next_generation(self, binding: SandboxBinding) -> SandboxBinding: ...


class DaytonaRuntime:
    """Public provider boundary for reusable root Sessions and child Environments."""

    def __init__(
        self,
        *,
        platform: SandboxPlatform,
        volume_client: VolumeClient,
        volume_config: VolumeConfig,
        bindings: BindingStoreLike,
        sandbox_spec: DaytonaSandboxSpec,
        client: Any,
        admission: DaytonaAdmission | None = None,
        cleanup: RunCleanupSupervisor | None = None,
        idle_stop_seconds: float | None = None,
        execution_output_cap: int = DEFAULT_EXECUTION_OUTPUT_CHARS,
        workspace_io_idle_seconds: float = 300.0,
        workspace_io_acquisition_timeout_seconds: float = 30.0,
        dispatcher: SyncBridgeDispatcher | None = None,
    ) -> None:
        self._tainted: set[tuple[str, str]] = set()
        self._active_idle_policies: dict[str, int] = {}
        self._sandbox_leases: set[SandboxResource] = set()
        self._records: dict[tuple[str, str], DaytonaSessionRecord] = {}
        self._lock = asyncio.Lock()
        self._key_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._invocation_gates: dict[tuple[str, str], asyncio.Lock] = {}
        self._state = DaytonaRuntimeState.OPEN
        # Late provider acquisitions stay tracked until their resulting
        # Sandbox is released, stopped, or deleted and the durable binding
        # is fenced. A timed-out or cancelled create never loses ownership.
        self._late_roots: dict[int, InterpreterLease] = {}
        self._acquisitions: set[asyncio.Task[Any]] = set()
        # Bounded provider-capacity admission owned by the runtime. The
        # semaphore is only held around provider operations, never across a
        # network call for an unrelated session (the registry lock is
        # released before the provider call in acquire_root_session).
        self._capacity = asyncio.Semaphore(8)

        self._platform = platform
        self._volume_client = volume_client
        self._volume_config = volume_config
        self._bindings = bindings
        self._binding_authority = BindingGenerationAuthority()
        self._active_leases = ActiveLeaseRegistry()
        self._admission = admission or DaytonaAdmission()
        self._dispatcher = dispatcher
        self._application_loop: asyncio.AbstractEventLoop | None = None
        self._sandbox_spec = sandbox_spec
        self._cleanup = cleanup or RunCleanupSupervisor()
        self._execution_output_cap = execution_output_cap
        if workspace_io_idle_seconds <= 0 or workspace_io_acquisition_timeout_seconds <= 0:
            raise ValueError("Workspace I/O timeouts must be positive")
        if idle_stop_seconds is not None and idle_stop_seconds <= 0:
            raise ValueError("idle_stop_seconds must be positive")
        self._idle_stop_seconds = idle_stop_seconds
        self._idle_tasks: dict[tuple[UUID, UUID], asyncio.Task[None]] = {}
        self._owned_sandbox_ids: set[str] = set()
        self._owned_sandbox_lock = Lock()
        self._late_owners: dict[int, _LateOwner] = {}
        self._provider_tasks: set[asyncio.Future[Any]] = set()
        self._client = client
        self._tracked_sandbox_ids: list[str] = []
        self._client_close_lock = Lock()
        self._client_close_task: asyncio.Task[Any] | None = None
        self._client_closed = False
        self._children = ChildSandboxes(
            platform=self._platform,
            admission=self._admission,
            dispatcher=self._dispatcher,
            provider_tasks=self._provider_tasks,
        )
        self._host_io = WorkspaceIO(
            lease_owner=self,
            platform=self._platform,
            volume_client=self._volume_client,
            volume_config=self._volume_config,
            sandbox_spec=self._sandbox_spec,
            admission=self._admission,
            provider_tasks=self._provider_tasks,
            leases=self._sandbox_leases,
            idle_seconds=workspace_io_idle_seconds,
            acquisition_timeout_seconds=workspace_io_acquisition_timeout_seconds,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        sandbox_spec: DaytonaSandboxSpec,
        bindings: BindingStoreLike,
        cleanup: RunCleanupSupervisor,
        max_active_leases: int,
        idle_stop_seconds: float | None,
        execution_output_cap: int,
        dispatcher: SyncBridgeDispatcher | None,
    ) -> DaytonaRuntime:
        """Construct one process-owned SDK graph within the runtime boundary."""
        environment_specs = {DaytonaEnvironmentProfile.SESSION: sandbox_spec}
        if getattr(settings, "daytona_child_snapshot", None):
            environment_specs[DaytonaEnvironmentProfile.SEMANTIC_CHILD] = sandbox_spec_from_settings(
                settings,
                DaytonaEnvironmentProfile.SEMANTIC_CHILD,
            )
        environment_specs[DaytonaEnvironmentProfile.WORKSPACE_CHILD] = DaytonaSandboxSpec(
            snapshot=sandbox_spec.snapshot,
            python_version=sandbox_spec.python_version,
            base_image=sandbox_spec.base_image,
            profile=DaytonaEnvironmentProfile.WORKSPACE_CHILD,
        )
        from fleet_rlm.daytona.provider import build_sync_daytona_client

        dispatcher = dispatcher or SyncBridgeDispatcher()
        dispatcher.sync_client_factory = lambda: build_sync_daytona_client(settings)
        client = build_daytona_client(settings)
        platform = LiveDaytonaPlatform(client, sandbox_spec, environment_specs)
        volume_client = LiveDaytonaVolumeClient(client)
        volume_config = volume_config_from_settings(settings)
        return cls(
            platform=platform,
            volume_client=volume_client,
            volume_config=volume_config,
            bindings=bindings,
            admission=DaytonaAdmission(max_active_leases=max_active_leases),
            sandbox_spec=sandbox_spec,
            cleanup=cleanup,
            idle_stop_seconds=idle_stop_seconds,
            execution_output_cap=execution_output_cap,
            workspace_io_idle_seconds=settings.workspace_io_idle_seconds,
            workspace_io_acquisition_timeout_seconds=settings.workspace_io_acquisition_timeout_seconds,
            dispatcher=dispatcher,
            client=client,
        )

    @property
    def volume_config(self) -> VolumeConfig:
        return self._volume_config

    @property
    def state(self) -> DaytonaRuntimeState:
        """Return the concrete runtime lifecycle state."""
        return self._state

    @property
    def roots(self) -> tuple[InterpreterLease, ...]:
        """Return a view of retained root leases."""
        return tuple(record.root for record in self._records.values() if record.root is not None)

    def owns_open_root(self, workspace_id: UUID | str | None, session_id: UUID | str) -> bool:
        """Return True when an OPEN root retains this Session."""
        sid = _identity_text(session_id, "session_id")
        if workspace_id is not None:
            try:
                record = self._records.get((_identity_text(workspace_id, "workspace_id"), sid))
            except ValueError:
                record = None
            if self._record_retains_root(record):
                return True
        return any(
            self._record_retains_root(record) and record.session_id == sid for record in tuple(self._records.values())
        )

    @staticmethod
    def _record_retains_root(record: DaytonaSessionRecord | None) -> bool:
        """Return whether a Session record still owns its provider Sandbox."""
        return (
            record is not None and record.root is not None and record.cleanup_state is not SessionCleanupState.RETIRED
        )

    async def begin_root_invocation(
        self, workspace_id: UUID, session_id: UUID, *, deadline: float
    ) -> Callable[[], None]:
        """Serialize one invocation for a retained Session Sandbox."""
        if self._state is not DaytonaRuntimeState.OPEN:
            raise RuntimeError("Daytona runtime is not accepting invocations")
        key = (str(workspace_id), str(session_id))
        gate = self._invocation_gates.setdefault(key, asyncio.Lock())
        async with asyncio.timeout_at(deadline):
            await gate.acquire()
        if self._state is not DaytonaRuntimeState.OPEN:
            gate.release()
            raise RuntimeError("Daytona runtime is not accepting invocations")
        self._records.setdefault(key, DaytonaSessionRecord(*key))
        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            gate.release()

        return release

    async def wait_for_session_idle(self, workspace_id: UUID, session_id: UUID, *, deadline: float) -> None:
        gate = self._invocation_gates.get((str(workspace_id), str(session_id)))
        if gate is not None:
            async with asyncio.timeout_at(deadline):
                async with gate:
                    pass

    async def _release_root(self, lease: InterpreterLease, *, deadline: float | None) -> None:
        if deadline is None:
            await self.release(lease)
            return
        async with asyncio.timeout_at(deadline):
            await self.release(lease)

    async def acquire_root_session(self, spec: RootSessionSpec) -> InterpreterLease:
        """Acquire or reuse the root for ``(workspace_id, session_id)``."""
        if not isinstance(spec, RootSessionSpec):
            raise TypeError("spec must be RootSessionSpec")
        if self._state is not DaytonaRuntimeState.OPEN:
            raise RuntimeError("Daytona runtime is not accepting root Sessions")

        key = spec.key
        async with self._lock:
            key_lock = self._key_locks.get(key)
            if key_lock is None:
                key_lock = asyncio.Lock()
                self._key_locks[key] = key_lock
        # One session's provider wait never blocks registry access for an
        # unrelated session: only this key is serialized, and the global
        # registry lock is held solely for short state checks and installs.
        async with key_lock:
            binding = await _provider_call(
                self._bindings.get(UUID(key[1])),
                deadline=spec.deadline,
                operation="binding lookup",
                owner=self._provider_tasks,
            )
            async with self._lock:
                if self._state is not DaytonaRuntimeState.OPEN:
                    raise RuntimeError("Daytona runtime is not accepting root Sessions")
                record = self._records.get(key)
                current = record.root if record is not None else None
                must_replace = current is not None and (
                    current.state is not LeaseState.OPEN
                    or key in self._tainted
                    or spec.force_new
                    or current.context_fingerprint != spec.context_fingerprint
                    or (
                        binding is not None
                        and (
                            binding.volume_subpath
                            != session_workspace_volume_subpath(
                                _coerce_uuid(spec.workspace_id, "workspace_id"),
                                _coerce_uuid(spec.session_id, "session_id"),
                            )
                            or binding.mount_path != SESSION_WORKSPACE_MOUNT_PATH
                        )
                    )
                )
                if current is None and key in self._tainted:
                    must_replace = True
                binding_changed = (
                    current is not None
                    and binding is not None
                    and (binding.sandbox_id != current.sandbox_id or binding.generation != current.binding_generation)
                )
                if current is not None and not must_replace and not binding_changed:
                    return current
                stale = current if must_replace or binding_changed else None

            if stale is not None:
                await self._release_root(stale, deadline=spec.deadline)
                async with self._lock:
                    record = self._records.get(key)
                    if record is not None and record.root is stale:
                        record.root = None
                        record.cleanup_state = SessionCleanupState.RETIRED

            try:
                owner = await self._acquire_root_from_provider(spec, force_new=must_replace or spec.force_new)
            except Exception as exc:
                mapped = map_daytona_sdk_error(exc)
                if mapped is exc:
                    raise
                raise mapped from exc
            async with self._lock:
                if self._state is DaytonaRuntimeState.OPEN:
                    self._tainted.discard(key)
                    self._sync_record(key, owner)
                    return owner
                self._late_roots[id(owner)] = owner
            await self._release_root(owner, deadline=None)
            self._late_roots.pop(id(owner), None)
            raise RuntimeError("Daytona runtime closed during root acquisition")

    async def discard_stale_root_session(
        self,
        workspace_id: UUID | str,
        session_id: UUID | str,
        *,
        deadline: float | None = None,
    ) -> None:
        """Close and drop a resident root after external replacement.

        The registry entry remains authoritative until the provider lease has
        closed successfully.  A replacement must not retire the remote
        Sandbox while this retained Root still owns its SessionManager lease.
        """
        key = (_identity_text(workspace_id, "workspace_id"), _identity_text(session_id, "session_id"))
        async with self._lock:
            record = self._records.get(key)
            owner = record.root if record is not None else None
            if owner is None:
                return
            self._mark_record(key, SessionCleanupState.RELEASING)
        try:
            await self._release_root(owner, deadline=deadline)
        except BaseException:
            async with self._lock:
                self._mark_record(key, SessionCleanupState.UNRESOLVED)
            raise
        async with self._lock:
            if record is not None and record.root is owner:
                record.root = None
            self._mark_record(key, SessionCleanupState.RETIRED)

    async def suspend_root_autostop(self, lease: InterpreterLease, *, deadline: float) -> None:
        """Preview traffic does not refresh Daytona's provider idle timer."""
        sandbox = lease.sandbox
        setter = getattr(sandbox, "set_autostop_interval", None)
        if not callable(setter):
            return  # Explicit fixture backends have no provider idle policy.
        previous = getattr(sandbox, "auto_stop_interval", None)
        if not isinstance(previous, int):
            await _provider_call(
                sandbox.refresh_data(),
                deadline=deadline,
                operation="Sandbox idle policy refresh",
                owner=self._provider_tasks,
            )
            previous = sandbox.auto_stop_interval
        if not isinstance(previous, int) or previous < 0:
            raise DaytonaAdapterError(message="Sandbox idle policy unavailable", cause_type="SandboxLifecycleError")
        self._active_idle_policies[lease.sandbox_id] = previous
        await _provider_call(
            setter(0), deadline=deadline, operation="Sandbox active idle policy", owner=self._provider_tasks
        )

    async def restore_root_autostop(self, lease: InterpreterLease) -> None:
        """Restore idle policy only on a healthy, released invocation."""
        previous = self._active_idle_policies.get(lease.sandbox_id)
        if previous is None:
            return
        key = (lease.workspace_id, lease.session_id)
        if key not in self._tainted:
            await _provider_call(
                lease.sandbox.set_autostop_interval(previous),
                deadline=time.monotonic() + 30.0,
                operation="Sandbox idle policy restoration",
                owner=self._provider_tasks,
            )
        self._active_idle_policies.pop(lease.sandbox_id, None)

    def mark_root_tainted(self, workspace_id: UUID | str, session_id: UUID | str) -> None:
        """Fence a root so the next acquisition rotates its generation."""
        key = (_identity_text(workspace_id, "workspace_id"), _identity_text(session_id, "session_id"))
        self._tainted.add(key)

    def root_is_tainted(self, workspace_id: UUID | str, session_id: UUID | str) -> bool:
        """Return whether the retained root Sandbox has been fenced."""
        key = (_identity_text(workspace_id, "workspace_id"), _identity_text(session_id, "session_id"))
        return key in self._tainted

    def session_record(self, workspace_id: UUID | str, session_id: UUID | str) -> DaytonaSessionRecord | None:
        """Return the runtime-owned resource record for one session, if any."""
        key = (_identity_text(workspace_id, "workspace_id"), _identity_text(session_id, "session_id"))
        return self._records.get(key)

    @property
    def has_pending_ownership(self) -> bool:
        """Whether roots, children, or late acquisitions remain unsettled."""
        return bool(
            any(record.root is not None for record in self._records.values())
            or self._children._child_cleanup_records
            or self._children._unidentified_child_sandboxes
            or self._children._child_factories
            or self._host_io._workspace_io_contexts
            or self._late_roots
            or self._acquisitions
            or any(gate.locked() for gate in self._invocation_gates.values())
            or self._owned_sandbox_ids
            or any(
                lease.state in {LeaseState.OPEN, LeaseState.CLOSING} or lease.has_pending_ownership
                for lease in self._sandbox_leases
            )
            or self._late_owners
            or any(not task.done() for task in self._provider_tasks)
            or any(not task.done() for task in self._idle_tasks.values())
        )

    def lifecycle_counts(self) -> dict[str, int]:
        """Content-free snapshot of tracked resources, deduplicated by Sandbox.

        Binding/session indexes and recovery-fence leases can refer to the same
        resource. Count it once, using the most conservative observed state.
        These process-local observations are not provider absence certificates.
        """
        counts = {"active": 0, "acquiring": 0, "draining": 0, "quarantined": 0}
        positions: dict[object, str] = {}
        rank = {"active": 0, "draining": 1, "quarantined": 2}

        def note(key: object, state: str) -> None:
            previous = positions.get(key)
            if previous is None or rank[state] > rank[previous]:
                positions[key] = state

        for record in self._records.values():
            root = record.root
            if root is None or root._provider_retired:
                continue
            state = "active"
            if record.cleanup_state is SessionCleanupState.UNRESOLVED or root.failed:
                state = "quarantined"
            elif root.closing or record.cleanup_state is SessionCleanupState.RELEASING:
                state = "draining"
            note(root.sandbox_id, state)
        for root in self._late_roots.values():
            if not root._provider_retired:
                note(root.sandbox_id, "quarantined" if root.failed else "draining")
        for record in self._children._child_cleanup_records.values():
            if record.permit._released:
                continue
            state = "active" if record.lease is not None else "draining"
            if record.lease is not None and record.lease.state is ChildRuntimeLeaseState.FAILED:
                state = "quarantined"
            elif record.close_task is not None:
                state = "draining" if not record.close_task.done() else "quarantined"
            note(record.sandbox_id, state)
        for _, sandbox, permit in self._children._unidentified_child_sandboxes:
            if not permit._released:
                note(id(sandbox), "quarantined")
        for lease in self._sandbox_leases:
            if lease.state is LeaseState.CLOSED and not lease.has_pending_ownership:
                continue
            state = "active"
            if lease.failed:
                state = "quarantined"
            elif lease.closing:
                state = "draining"
            note(lease._sandbox_id or id(lease), state)
        for owner in self._late_owners.values():
            if owner.lease is not None and not owner.lease._provider_retired:
                note(owner.lease.sandbox_id, "quarantined" if owner.lease.failed else "draining")
        for sandbox_id in self._tracked_sandbox_ids:
            if sandbox_id not in positions:
                note(sandbox_id, "draining" if self._state is not DaytonaRuntimeState.OPEN else "active")
        counts["acquiring"] = sum(not task.done() for task in self._acquisitions)
        counts["acquiring"] += sum(
            not task.done() and task.get_name() in {"fleet-daytona-child-create", "fleet-daytona-workspace-io-create"}
            for task in self._provider_tasks
            if isinstance(task, asyncio.Task)
        )
        for state in positions.values():
            counts[state] += 1
        return counts

    def _retain_sandbox_resource(self, lease: SandboxResource) -> None:
        self._sandbox_leases.add(lease)

    def _release_settled_sandbox_resource(self, lease: SandboxResource) -> None:
        if lease.state is not LeaseState.OPEN and not lease.has_pending_ownership:
            self._sandbox_leases.discard(lease)

    def _sync_record(self, key: tuple[str, str], owner: InterpreterLease) -> None:
        """Install the root in its single authoritative Session record."""
        record = self._records.get(key)
        if record is None:
            record = DaytonaSessionRecord(workspace_id=key[0], session_id=key[1])
            self._records[key] = record
        record.root = owner
        record.cleanup_state = SessionCleanupState.ACTIVE

    def _mark_record(self, key: tuple[str, str], state: SessionCleanupState) -> None:
        record = self._records.get(key)
        if record is not None:
            record.cleanup_state = state

    def _retain_late_acquisition(self, acquisition: asyncio.Task[InterpreterLease]) -> None:
        """Retain the actual provider operation and close its unpublished result."""

        async def settle() -> None:
            try:
                owner = await asyncio.shield(acquisition)
            except BaseException:
                # Provider failure has no returned lease. The provider's
                # acquisition path retains any partially created resource.
                return
            self._late_roots[id(owner)] = owner
            try:
                await self._release_root(owner, deadline=None)
            except BaseException:
                # Keep the concrete lease for shutdown retry.
                return
            self._late_roots.pop(id(owner), None)

        task = asyncio.create_task(settle(), name="fleet-daytona-late-acquisition-cleanup")
        _retain_provider_task(task, self._provider_tasks)

    async def close_root_session(
        self,
        workspace_id: UUID | str,
        session_id: UUID | str,
        *,
        deadline: float | None = None,
    ) -> None:
        """Close one retained root."""
        key = (_identity_text(workspace_id, "workspace_id"), _identity_text(session_id, "session_id"))
        async with self._lock:
            record = self._records.get(key)
            owner = record.root if record is not None else None
        if owner is None:
            return
        async with self._lock:
            self._mark_record(key, SessionCleanupState.RELEASING)
        try:
            await self._release_root(owner, deadline=deadline)
        except BaseException:
            async with self._lock:
                self._mark_record(key, SessionCleanupState.UNRESOLVED)
            raise
        async with self._lock:
            if owner.closed:
                self._mark_record(key, SessionCleanupState.RETIRED)

    async def aclose(self, *, deadline: float | None = None, drain_seconds: float = 30.0) -> bool:
        """Close all retained roots and active children.

        Registry entries remain owned until their close is confirmed.  This is
        important when cancellation or a provider failure interrupts shutdown:
        a later close call must be able to retry the same lease instead of
        losing the only reference to it.
        """
        if drain_seconds < 0:
            raise ValueError("drain_seconds must be non-negative")
        if deadline is None:
            deadline = asyncio.get_running_loop().time() + drain_seconds
        self._state = DaytonaRuntimeState.CLOSING
        self._host_io.accepting = False
        self._children.accepting = False
        errors: list[BaseException] = []
        await self._host_io._cancel_workspace_io_idle()

        while self._host_io._workspace_io_contexts and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(min(0.05, max(0.0, deadline - asyncio.get_running_loop().time())))
        if self._host_io._workspace_io_contexts:
            return False

        for lease in self._host_io._workspace_io_resources():
            try:
                await self._host_io._close_workspace_io_lease(lease, deadline=deadline)
            except BaseException as exc:
                errors.append(exc)

        for factory in tuple(self._children._child_factories):
            task = asyncio.create_task(asyncio.to_thread(factory.wait_owned), name="fleet-daytona-child-drain")
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            _done, pending = await asyncio.wait({task}, timeout=remaining)
            if pending:
                return False
            try:
                task.result()
            except BaseException as exc:
                errors.append(exc)
            else:
                self._children._child_factories.discard(factory)

        try:
            async with asyncio.timeout_at(deadline):
                for gate in tuple(self._invocation_gates.values()):
                    async with gate:
                        pass
        except TimeoutError:
            return False

        acquisitions = tuple(self._acquisitions)
        if acquisitions:
            timeout = None if deadline is None else max(0.0, deadline - asyncio.get_running_loop().time())
            await asyncio.wait(acquisitions, timeout=timeout)

        async with self._lock:
            child_records = tuple(
                record for record in self._children._child_cleanup_records.values() if record.lease is not None
            )
            roots = tuple((record.key, record.root) for record in self._records.values() if record.root is not None)

        for record in child_records:
            lease = record.lease
            assert lease is not None
            task = record.close_task
            if task is None or task.done():
                if lease.state is ChildRuntimeLeaseState.FAILED:
                    continue
                task = asyncio.create_task(asyncio.to_thread(lease.close), name="fleet-daytona-child-lease-close")
                record.close_task = task
                _retain_provider_task(task, self._provider_tasks)
        child_close_tasks = tuple(
            record.close_task
            for record in self._children._child_cleanup_records.values()
            if record.close_task is not None and not record.close_task.done()
        )
        if child_close_tasks:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            completed_child_closes, pending_child_closes = await asyncio.wait(child_close_tasks, timeout=remaining)
            for task in completed_child_closes:
                with contextlib.suppress(BaseException):
                    task.result()
            if pending_child_closes:
                return False

        await self._children._retry_child_cleanup_records(deadline=deadline)

        for key, root in roots:
            assert root is not None
            try:
                async with asyncio.timeout_at(deadline):
                    await self.release(root)
            except BaseException as exc:
                errors.append(exc)
                async with self._lock:
                    self._mark_record(key, SessionCleanupState.UNRESOLVED)
            else:
                if root.closed:
                    async with self._lock:
                        record = self._records.get(key)
                        if record is not None and record.root is root:
                            record.root = None
                        self._mark_record(key, SessionCleanupState.RETIRED)

        for owner in tuple(self._late_roots.values()):
            try:
                async with asyncio.timeout_at(deadline):
                    await self.release(owner)
            except BaseException as exc:
                errors.append(exc)
            else:
                self._late_roots.pop(id(owner), None)

        provider_settled = await self._drain_provider_ownership(
            drain_seconds=max(0.0, deadline - asyncio.get_running_loop().time())
        )
        async with self._lock:
            retained = self.has_pending_ownership or not provider_settled
            if not retained:
                self._key_locks.clear()
                self._invocation_gates.clear()
                self._host_io._warm_workspace_io_sandboxes.clear()
        self._state = DaytonaRuntimeState.FAILED if errors or retained else DaytonaRuntimeState.CLOSED
        logger.info("Daytona shutdown ownership: %s", self.lifecycle_counts())
        return not errors and not retained

    def open_workspace_sandbox(self, workspace_id: UUID, *, purpose: str, reuse_warm: bool = False):
        """Open a serialized host-I/O operation owned by the runtime."""
        return self._host_io.open_workspace_sandbox(workspace_id, purpose=purpose, reuse_warm=reuse_warm)

    def schedule_workspace_prewarm(self, workspace_id: UUID) -> asyncio.Task[None] | None:
        """Pre-warm the Workspace I/O Sandbox that a Turn in this Workspace will open first."""
        return self._host_io.schedule_workspace_prewarm(workspace_id)

    def build_child_factory(
        self,
        *,
        volume_id: str | None,
        workspace_id: UUID,
        run_id: UUID,
        deadline: float,
        execution_output_cap: int,
        session_id: UUID | None = None,
        is_authorized: Callable[[], bool] | None = None,
        profile: DaytonaEnvironmentProfile = DaytonaEnvironmentProfile.WORKSPACE_CHILD,
        semantic_child_available: bool = True,
    ) -> Callable[..., Any]:
        """Bind child acquisition to this runtime's owning event loop."""
        self._application_loop = asyncio.get_running_loop()
        return self._children.build_child_factory(
            volume_id=volume_id,
            workspace_id=workspace_id,
            run_id=run_id,
            deadline=deadline,
            execution_output_cap=execution_output_cap,
            session_id=session_id,
            is_authorized=is_authorized,
            profile=profile,
            semantic_child_available=semantic_child_available,
        )

    def track_sandbox(self, sandbox_id: str | None) -> None:
        """Retain a concrete Sandbox identity until process disposal confirms absence."""
        if sandbox_id and sandbox_id not in self._tracked_sandbox_ids:
            self._tracked_sandbox_ids.append(sandbox_id)

    async def _cleanup_tracked_sandboxes(self, *, deadline: float) -> bool:
        retained: list[str] = []
        for sandbox_id in tuple(self._tracked_sandbox_ids):
            if self.owns_sandbox(sandbox_id):
                retained.append(sandbox_id)
                continue
            try:
                await _provider_call(
                    self._platform.delete(sandbox_id),
                    deadline=deadline,
                    operation="tracked Sandbox delete",
                    owner=self._provider_tasks,
                )
            except Exception as exc:
                if not is_sandbox_not_found(exc):
                    retained.append(sandbox_id)
                    continue
            try:
                observed = await _provider_call(
                    self._platform.get(sandbox_id),
                    deadline=deadline,
                    operation="tracked Sandbox absence probe",
                    owner=self._provider_tasks,
                )
            except Exception as exc:
                if not is_sandbox_not_found(exc):
                    retained.append(sandbox_id)
            else:
                if observed is not None:
                    retained.append(sandbox_id)
        self._tracked_sandbox_ids = retained
        return not retained

    async def _close_client(self, *, deadline: float) -> bool:
        if self._client is None or self._client_closed:
            return True
        with self._client_close_lock:
            task = self._client_close_task
            if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
                task = asyncio.create_task(self._client.close(), name="fleet-daytona-client-close")
                self._client_close_task = task
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, deadline - asyncio.get_running_loop().time()))
        except TimeoutError:
            return False
        else:
            self._client_closed = True
            return True

    def has_pending_cleanup(self) -> bool:
        return bool(
            self._tracked_sandbox_ids
            or self._host_io._workspace_io_resources()
            or any(not task.done() for task in self._provider_tasks)
            or (self._client_close_task is not None and not self._client_close_task.done())
            or self.has_pending_ownership
        )

    async def wait_pending_cleanup(self, *, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else asyncio.get_running_loop().time() + timeout
        for resource in tuple(self._sandbox_leases):
            if resource.failed:
                try:
                    await resource.aclose(deadline=deadline)
                except TimeoutError:
                    return False
        lease_tasks = tuple(
            task
            for lease in self._sandbox_leases
            for task in (lease._close_task,)
            if task is not None and not task.done()
        )
        tasks = tuple(
            task
            for task in (
                *self._provider_tasks,
                *lease_tasks,
                self._client_close_task,
            )
            if task is not None and not task.done()
        )
        if tasks:
            if any(task.get_loop() is not asyncio.get_running_loop() for task in tasks):
                return False
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            if pending:
                return False
        return not self.has_pending_cleanup()

    async def adispose(self, *, drain_seconds: float = 30.0) -> bool:
        """Settle runtime resources before closing the process-owned SDK client."""
        deadline = asyncio.get_running_loop().time() + drain_seconds
        settled = await self.aclose(deadline=deadline)
        tracked = await self._cleanup_tracked_sandboxes(deadline=deadline)
        if self.has_pending_cleanup():
            await self.wait_pending_cleanup(timeout=max(0.0, deadline - asyncio.get_running_loop().time()))
        if not settled or not tracked or self.has_pending_cleanup() or self.has_pending_ownership:
            return False
        closed = await self._close_client(deadline=deadline)
        logger.info("Daytona disposal ownership: %s", self.lifecycle_counts())
        return closed

    async def close(self, *, deadline: float | None = None) -> bool:
        return await self.aclose(deadline=deadline)

    async def _acquire_root_from_provider(self, spec: RootSessionSpec, *, force_new: bool) -> InterpreterLease:
        if spec.deadline is not None and spec.deadline <= asyncio.get_running_loop().time():
            raise TimeoutError("root Session acquisition timed out")

        async def _call() -> InterpreterLease:
            # Capacity is admitted only around this provider operation.
            # The registry lock is not held here (acquire_root_session
            # releases it before this call), so one session's network wait
            # never blocks an unrelated session's registry access.
            async with _admitted(self._capacity, spec.deadline):
                lease = await self.acquire(
                    LeaseRequest(
                        session_id=_coerce_uuid(spec.session_id, "session_id"),
                        user_id=_coerce_uuid(spec.user_id or uuid4(), "user_id"),
                        workspace_id=_coerce_uuid(spec.workspace_id, "workspace_id"),
                        run_id=_coerce_uuid(spec.run_id, "run_id") if spec.run_id is not None else None,
                    ),
                    deadline=spec.deadline if spec.deadline is not None else float("inf"),
                    force_new=force_new,
                )
                if lease.sandbox is None:
                    await self.release(lease)
                    raise RuntimeError("acquired Daytona Sandbox is unavailable")
                lease.context_fingerprint = spec.context_fingerprint
                return lease

        acquisition = asyncio.create_task(_call(), name="fleet-daytona-root-acquisition")
        self._acquisitions.add(acquisition)
        try:
            if spec.deadline is None:
                return await asyncio.shield(acquisition)
            remaining = max(0.0, spec.deadline - asyncio.get_running_loop().time())
            return await asyncio.wait_for(asyncio.shield(acquisition), timeout=remaining)
        except (TimeoutError, asyncio.CancelledError):
            self._retain_late_acquisition(acquisition)
            raise
        finally:
            self._acquisitions.discard(acquisition)

    @property
    def active_leases(self) -> ActiveLeaseRegistry:
        return self._active_leases

    def _idle_stop_blocked(self, session_id: UUID, workspace_id: UUID | None) -> bool:
        if self._active_leases.holder(session_id, workspace_id=workspace_id) is not None:
            return True
        if self._active_leases.has_session(session_id):
            return True
        try:
            return self.owns_open_root(workspace_id, session_id)
        except (ValueError, TypeError):
            return True

    def _observe_binding(self, binding: SandboxBinding | None) -> None:
        if binding is None:
            return
        self._binding_authority.observe(binding)

    def is_binding_current(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        sandbox_id: str,
        generation: int,
    ) -> bool:
        return self._binding_authority.is_current(
            session_id=session_id,
            workspace_id=workspace_id,
            sandbox_id=sandbox_id,
            generation=generation,
        )

    def revoke_binding(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        sandbox_id: str,
        generation: int,
    ) -> None:
        self._binding_authority.revoke(
            session_id=session_id,
            workspace_id=workspace_id,
            sandbox_id=sandbox_id,
            generation=generation,
        )

    def _mark_sandbox_owned(self, sandbox_id: str) -> None:
        with self._owned_sandbox_lock:
            self._owned_sandbox_ids.add(sandbox_id)

    def _mark_sandbox_released(self, sandbox_id: str) -> None:
        with self._owned_sandbox_lock:
            self._owned_sandbox_ids.discard(sandbox_id)

    def owns_sandbox(self, sandbox_id: str) -> bool:
        with self._owned_sandbox_lock:
            return sandbox_id in self._owned_sandbox_ids

    async def prewarm_session(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        deadline: float | None = None,
    ) -> bool:
        effective_deadline = deadline if deadline is not None else asyncio.get_running_loop().time() + 120.0
        try:
            lease = await self.acquire(
                LeaseRequest(
                    session_id=session_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    run_id=PREWARM_RUN_ID,
                ),
                deadline=effective_deadline,
            )
        except ActiveLeaseConflictError:
            return False
        await self.release(lease)
        return True

    def schedule_prewarm(
        self,
        session_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
    ) -> asyncio.Task[None]:
        # The Workspace I/O Sandbox is the slow cold open on a Turn's first preparation step, so
        # the session-create trigger warms it alongside the session root lease.
        self.schedule_workspace_prewarm(workspace_id)

        async def run_prewarm() -> None:
            try:
                await self.prewarm_session(session_id, user_id=user_id, workspace_id=workspace_id)
            except asyncio.CancelledError:
                raise
            except BaseException:
                pass

        task = asyncio.create_task(run_prewarm(), name=f"fleet-session-prewarm-{session_id}")
        _retain_provider_task(task, self._provider_tasks)
        return task

    def _expected_mount(self, *, volume_id: str, workspace_id: UUID) -> ExpectedWorkspaceMount:
        return _expected_workspace_mount(self._volume_config, volume_id, workspace_id)

    def _expected_execution_mount(
        self, *, volume_id: str, workspace_id: UUID, session_id: UUID
    ) -> ExpectedWorkspaceMount:
        return ExpectedWorkspaceMount(
            volume_id=str(volume_id),
            volume_subpath=session_workspace_volume_subpath(workspace_id, session_id),
            mount_path=SESSION_WORKSPACE_MOUNT_PATH,
            workspace_id=workspace_id,
            session_id=session_id,
        )

    def _sandbox_retirement_lease(
        self,
        sandbox_id: str,
        *,
        confirm_timeout_s: float = 120.0,
    ) -> SandboxResource:
        for lease in self._sandbox_leases:
            if (
                lease._sandbox is None
                and lease._sandbox_id == sandbox_id
                and lease.role == "root"
                and (lease.state in {LeaseState.OPEN, LeaseState.CLOSING} or lease.has_pending_ownership)
            ):
                return lease
        return SandboxResource(
            owner=self,
            sandbox=None,
            sandbox_id=sandbox_id,
            platform=self._platform,
            role="root",
            timeout_s=confirm_timeout_s,
        )

    async def acquire(
        self,
        request: LeaseRequest,
        *,
        deadline: float,
        force_new: bool = False,
    ) -> InterpreterLease:
        require_non_zero_workspace_id(request.workspace_id)
        run_id = request.run_id or uuid4()
        session_id = request.session_id
        await self._cancel_idle_stop(session_id, workspace_id=request.workspace_id, deadline=deadline)
        await _claim_session_lease(
            self._active_leases, session_id, run_id, workspace_id=request.workspace_id, deadline=deadline
        )
        claim_held = True
        permit: DaytonaAdmissionPermit | None = None
        try:
            permit = await self._admission.acquire(deadline=deadline)
            acquisition = asyncio.create_task(
                self._acquire_provider(request, run_id=run_id, deadline=deadline, force_new=force_new),
                name="fleet-daytona-provider-acquisition",
            )
            try:
                async with asyncio.timeout_at(deadline):
                    lease = await asyncio.shield(acquisition)
            except TimeoutError:
                self._adopt_late_acquisition(acquisition, permit, request, run_id)
                permit = None
                claim_held = False
                raise DaytonaLeaseAcquisitionTimeoutError("Daytona lease acquisition timed out") from None
            except asyncio.CancelledError:
                self._adopt_late_acquisition(acquisition, permit, request, run_id)
                permit = None
                claim_held = False
                raise

            self._bind_lease_ownership(
                lease,
                permit,
                session_id=session_id,
                workspace_id=request.workspace_id,
                run_id=run_id,
            )
            self._mark_sandbox_owned(lease.sandbox_id)
            return lease
        except BaseException:
            try:
                if permit is not None:
                    permit.release()
            finally:
                if claim_held:
                    self._active_leases.release(session_id, run_id, workspace_id=request.workspace_id)
            raise

    @staticmethod
    async def _settle_provider_acquisition(acquisition: asyncio.Task[InterpreterLease]) -> InterpreterLease:
        return await _settle_provider_task(acquisition)

    async def _settle_late_owner(self, owner: _LateOwner, *, deadline: float | None = None) -> None:
        if owner.unpublished:
            await self._finish_unpublished_lease(owner, deadline=deadline)
            return
        if owner.acquisition is not None and owner.lease is None:
            await self._settle_late_acquisition(owner)
            return
        await self._settle_late_lease(owner)

    async def _settle_late_acquisition(self, owner: _LateOwner) -> None:
        acquisition = owner.acquisition
        assert acquisition is not None
        if not acquisition.done():
            try:
                acquisition_loop = acquisition.get_loop()
            except BaseException:
                acquisition_loop = None
            if acquisition_loop is not asyncio.get_running_loop():
                return
        try:
            try:
                lease = await self._settle_provider_acquisition(acquisition)
            except BaseException:
                try:
                    if owner.permit is not None:
                        owner.permit.release()
                finally:
                    self._active_leases.release(
                        owner.request.session_id,
                        owner.run_id,
                        workspace_id=owner.request.workspace_id,
                    )
                return
            owner.lease = lease
            owner.acquisition = None
            if self._late_owners.get(id(acquisition)) is owner:
                self._late_owners.pop(id(acquisition), None)
            self._late_owners[id(lease)] = owner
            self._mark_sandbox_owned(lease.sandbox_id)
            await self._settle_late_lease(owner)
        finally:
            if acquisition.done() and self._late_owners.get(id(acquisition)) is owner:
                self._late_owners.pop(id(acquisition), None)

    def _track_late_cleanup(self, owner: _LateOwner, task: Any) -> None:
        owner.cleanup_task = task
        _retain_provider_task(task, self._provider_tasks)
        task.add_done_callback(self._settled_late_cleanup)

    def _schedule_late_owner_fallback(self, owner: _LateOwner) -> bool:
        try:
            owner_loop = owner.acquisition.get_loop() if owner.acquisition is not None else None
        except BaseException:
            owner_loop = None
        if owner_loop is None or owner_loop.is_closed() or not owner_loop.is_running():
            # The late-owner registry retains the resource for a later drain;
            # never move a loop-bound acquisition/client to a fabricated loop.
            return False
        try:
            execution = schedule_owned_close(
                loop=owner_loop,
                build=lambda: self._settle_late_owner(owner),
            )
        except BaseException as exc:
            logger.critical("unable to retain late Daytona ownership cleanup", extra={"error_type": type(exc).__name__})
            return False
        self._track_late_cleanup(owner, execution.future)
        return True

    def _schedule_late_owner(self, owner: _LateOwner) -> bool:
        if owner.cleanup_task is not None and not owner.cleanup_task.done():
            return True
        awaitable = self._settle_late_owner(owner)
        try:
            task = self._cleanup.submit(awaitable)
        except BaseException:
            try:
                task = asyncio.create_task(awaitable, name="fleet-daytona-late-ownership-cleanup")
            except BaseException:
                with contextlib.suppress(BaseException):
                    awaitable.close()
                return self._schedule_late_owner_fallback(owner)
        self._track_late_cleanup(owner, task)
        return True

    def _adopt_late_acquisition(
        self,
        acquisition: asyncio.Task[InterpreterLease],
        permit: DaytonaAdmissionPermit,
        request: LeaseRequest,
        run_id: UUID,
    ) -> None:
        owner = _LateOwner(
            request=request,
            run_id=run_id,
            permit=permit,
            acquisition=acquisition,
        )
        self._late_owners[id(acquisition)] = owner
        if self._schedule_late_owner(owner):
            return

        def retry_after_settlement(_completed: asyncio.Future[Any]) -> None:
            if owner.cleanup_task is None:
                self._schedule_late_owner(owner)

        acquisition.add_done_callback(retry_after_settlement)

    async def _settle_late_lease(self, owner: _LateOwner) -> None:
        lease = owner.lease
        assert lease is not None
        assert owner.permit is not None
        try:
            release_task = asyncio.create_task(asyncio.to_thread(lease.release))
            await _settle_provider_task(release_task)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

        quarantine_error: BaseException | None = None
        # A failed interpreter/broker release cannot discard remote ownership.
        # Fence and retire the sandbox anyway; otherwise a transient local
        # shutdown error leaves the admission slot and provider resource live.
        try:
            await self._quarantine(
                lease,
                LeaseRequest(
                    session_id=owner.request.session_id,
                    user_id=owner.request.user_id,
                    workspace_id=UUID(str(lease.workspace_id)) if lease.workspace_id else UUID(int=0),
                    run_id=owner.run_id,
                ),
            )
        except asyncio.CancelledError as exc:
            quarantine_error = exc
        except Exception as exc:
            quarantine_error = exc

        if quarantine_error is not None:
            return

        owner.permit.release()
        self._active_leases.release(
            owner.request.session_id,
            owner.run_id,
            workspace_id=owner.request.workspace_id,
        )
        self._mark_sandbox_released(lease.sandbox_id)
        self._late_owners.pop(id(lease), None)

    async def _retry_late_owners(self, deadline: float) -> bool:
        current_loop = asyncio.get_running_loop()
        tasks: list[asyncio.Future[Any]] = []
        for owner in {id(o): o for o in self._late_owners.values()}.values():
            if owner.unpublished:
                awaitable = self._settle_late_owner(owner, deadline=deadline)
                try:
                    task = asyncio.create_task(awaitable, name="fleet-daytona-unpublished-lease-retry")
                except BaseException:
                    with contextlib.suppress(BaseException):
                        awaitable.close()
                    continue
                self._track_late_cleanup(owner, task)
                tasks.append(task)
                continue
            if owner.acquisition is not None and owner.lease is None:
                if not owner.acquisition.done():
                    try:
                        acquisition_loop = owner.acquisition.get_loop()
                    except BaseException:
                        acquisition_loop = None
                    if acquisition_loop is not current_loop:
                        continue
                task = owner.cleanup_task
                if task is None or task.done():
                    self._schedule_late_owner(owner)
                    task = owner.cleanup_task
                if task is not None:
                    tasks.append(task if isinstance(task, asyncio.Future) else asyncio.wrap_future(task))
                continue
            task = owner.cleanup_task
            if task is None or task.done():
                task = asyncio.create_task(self._settle_late_lease(owner), name="fleet-daytona-late-lease-retry")
                self._track_late_cleanup(owner, task)
            tasks.append(task)
        if not tasks:
            return not self._late_owners
        remaining = max(0.0, deadline - current_loop.time())
        _, pending = await asyncio.wait(tuple(tasks), timeout=remaining)
        return not pending and not self._late_owners

    def _settled_late_cleanup(self, task: Any) -> None:
        if task.cancelled():
            return
        with contextlib.suppress(BaseException):
            error = task.exception()
        if error is not None:
            logger.warning("late Daytona ownership cleanup failed", extra={"error_type": type(error).__name__})

    async def _quarantine(
        self, lease: InterpreterLease, request: LeaseRequest, *, deadline: float | None = None
    ) -> None:
        if lease.requires_sandbox_deletion:
            await self._persist_native_binding_state(
                lease,
                request,
                provider_state="fencing",
                deadline=deadline,
            )
            timeout_s = 30.0
            if deadline is not None:
                timeout_s = max(0.1, min(timeout_s, deadline - asyncio.get_running_loop().time()))
            retirement = self._sandbox_retirement_lease(lease.sandbox_id, confirm_timeout_s=timeout_s)
            receipt = await retirement.aclose()
            if not receipt:
                raise RuntimeError("native sandbox deletion was not confirmed")
            await self._persist_native_binding_state(
                lease,
                request,
                provider_state="quarantined",
                deadline=deadline,
            )
            return
        await self._fence_binding(
            SandboxBinding(
                session_id=request.session_id,
                sandbox_id=lease.sandbox_id,
                workspace_id=request.workspace_id,
                volume_id=lease.volume_id,
                volume_subpath=lease.volume_subpath or workspace_volume_subpath(request.workspace_id),
                mount_path=lease.mount_path,
                provider_state="running",
                generation=lease.binding_generation,
            ),
            deadline=deadline,
        )
        if lease.created_sandbox:
            retire = self._sandbox_retirement_lease(lease.sandbox_id)
            receipt_box: dict[str, bool] = {}

            async def _retire() -> None:
                receipt_box["receipt"] = await retire.aclose()

            deletion = _retire()
            try:
                deletion_task = self._cleanup.submit(deletion)
            except BaseException as scheduler_error:
                try:
                    deletion_task = asyncio.create_task(deletion, name="fleet-daytona-late-sandbox-retirement")
                except BaseException:
                    with contextlib.suppress(BaseException):
                        deletion.close()
                    raise RuntimeError("sandbox retirement ownership unavailable") from scheduler_error
            await deletion_task
            receipt = receipt_box["receipt"]
            if not receipt:
                raise RuntimeError("sandbox retirement was not confirmed")

    async def _persist_native_binding_state(
        self,
        lease: InterpreterLease,
        request: LeaseRequest,
        *,
        provider_state: str,
        deadline: float | None = None,
    ) -> None:
        binding = await self._get_binding_for_workspace(
            request.session_id,
            request.workspace_id,
            deadline=deadline,
        )
        if binding is None or binding.sandbox_id != lease.sandbox_id or binding.generation != lease.binding_generation:
            return
        persisted = await _provider_call(
            self._bindings.upsert(replace(binding, provider_state=provider_state, last_verified_at=datetime.now(UTC))),
            deadline=deadline,
            operation=f"Native Sandbox {provider_state} persistence",
            owner=self._provider_tasks,
        )
        self._observe_binding(persisted)

    async def _get_binding_for_workspace(
        self,
        session_id: UUID,
        workspace_id: UUID,
        *,
        deadline: float | None = None,
    ) -> SandboxBinding | None:
        async def read(awaitable: Awaitable[Any]) -> Any:
            return await _provider_call(
                awaitable,
                deadline=deadline,
                operation="Sandbox binding lookup",
                owner=self._provider_tasks,
            )

        binding = await read(self._bindings.get_scoped(session_id, workspace_id=workspace_id))
        if binding is not None:
            self._observe_binding(binding)
            return binding
        unscoped = await read(self._bindings.get(session_id))
        if unscoped is not None:
            raise DaytonaAdapterError(
                message="sandbox binding does not match workspace scope",
                cause_type="WorkspaceMountMismatch",
            )
        return None

    async def fence_session(
        self,
        session_id: UUID,
        *,
        workspace_id: UUID | None = None,
        deadline: float | None = None,
    ) -> None:
        if workspace_id is not None:
            binding = await self._get_binding_for_workspace(session_id, workspace_id, deadline=deadline)
        else:
            binding = await _provider_call(
                self._bindings.get(session_id),
                deadline=deadline,
                operation="Sandbox binding lookup",
                owner=self._provider_tasks,
            )
        stops = [
            self._fence_execution_sandbox(record.sandbox_id, deadline=deadline)
            for record in tuple(self._children._child_cleanup_records.values())
            if record.session_id == session_id
        ]
        if binding is not None and binding.sandbox_id:
            self.mark_root_tainted(binding.workspace_id, session_id)
            stops.append(self._fence_binding(binding, deadline=deadline))
        results = await asyncio.gather(*stops, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def _fence_binding(self, binding: SandboxBinding, *, deadline: float | None = None) -> None:
        fenced = await _provider_call(
            self._bindings.upsert(replace(binding, provider_state="fencing", last_verified_at=datetime.now(UTC))),
            deadline=deadline,
            operation="Sandbox fence persistence",
            owner=self._provider_tasks,
        )
        self._observe_binding(fenced)
        if binding.sandbox_id is None:
            return
        await self._fence_execution_sandbox(binding.sandbox_id, deadline=deadline)
        quarantined = await _provider_call(
            self._bindings.upsert(replace(binding, provider_state="quarantined", last_verified_at=datetime.now(UTC))),
            deadline=deadline,
            operation="Sandbox quarantine persistence",
            owner=self._provider_tasks,
        )
        self._observe_binding(quarantined)

    async def _fence_execution_sandbox(self, sandbox_id: str, *, deadline: float | None = None) -> None:
        """Force-stop without waiting for the interpreter; keep existing lease ownership."""
        timeout_s = 30.0
        if deadline is not None:
            timeout_s = max(0.1, min(timeout_s, deadline - asyncio.get_running_loop().time()))
        fence_lease = next(
            (
                resource
                for resource in self._sandbox_leases
                if resource._sandbox_id == sandbox_id and resource.role == "fence"
            ),
            None,
        )
        if fence_lease is None:
            fence_lease = SandboxResource(
                owner=self,
                sandbox=None,
                sandbox_id=sandbox_id,
                platform=self._platform,
                role="fence",
                timeout_s=timeout_s,
            )

        async def _fenced_stop() -> None:
            receipt = await fence_lease.aclose()
            if not receipt:
                raise RuntimeError("Sandbox fence failed") from fence_lease.failure

        await _provider_call(
            _fenced_stop(),
            deadline=deadline,
            operation="Sandbox fencing",
            owner=self._provider_tasks,
        )

    def _bind_lease_ownership(
        self,
        lease: InterpreterLease,
        permit: DaytonaAdmissionPermit,
        *,
        session_id: UUID,
        workspace_id: UUID,
        run_id: UUID,
    ) -> None:
        def _clear_active() -> None:
            try:
                permit.release()
            finally:
                self._mark_sandbox_released(lease.sandbox_id)
                self._active_leases.release(session_id, run_id, workspace_id=workspace_id)

        lease._on_release = _clear_active

    async def _acquire_provider(
        self,
        request: LeaseRequest,
        *,
        run_id: UUID,
        deadline: float | None = None,
        force_new: bool = False,
    ) -> InterpreterLease:
        context: _AcquisitionContext | None = None
        sandbox: Any | None = None
        created_sandbox = False
        try:
            context = await self._resolve_acquisition_context(request, deadline=deadline)
            sandbox, created_sandbox = await self._prepare_sandbox(
                request,
                context,
                deadline=deadline,
                force_new=force_new,
            )
            try:
                await self._verify_run_layout(
                    sandbox, context.expected, request.session_id, run_id, created_sandbox, deadline=deadline
                )
            except DaytonaAdapterError as exc:
                if exc.cause_type != "ExecutionMountNotVisible":
                    raise
                if not created_sandbox:
                    raise
                failed_sandbox = sandbox
                sandbox = None
                retired = await self._cleanup_failed_acquisition(
                    request,
                    failed_sandbox,
                    created_sandbox=True,
                    deadline=deadline,
                    binding=context.persisted_binding,
                )
                if not retired:
                    raise DaytonaAdapterError(
                        message="Sandbox with missing Workspace mount could not be retired",
                        cause_type="SandboxRetirementUnconfirmed",
                    ) from exc
                sandbox = await self._create_sandbox(
                    volume_id=context.expected.volume_id,
                    mount_path=context.expected.mount_path,
                    volume_subpath=context.expected.volume_subpath,
                    request=request,
                    deadline=deadline,
                )
                created_sandbox = True
                await self._verify_run_layout(
                    sandbox, context.expected, request.session_id, run_id, created_sandbox, deadline=deadline
                )
            return await self._persist_binding_and_build_lease(
                request,
                run_id,
                context.expected,
                sandbox,
                created_sandbox,
                deadline=deadline,
                context=context,
            )
        except _ProviderCallDeadlineError as exc:
            with contextlib.suppress(BaseException):
                await _settle_provider_task(exc.task)
            if context is not None and sandbox is not None:
                await self._cleanup_failed_acquisition(
                    request,
                    sandbox,
                    created_sandbox=created_sandbox,
                    deadline=deadline,
                    binding=context.persisted_binding,
                )
            raise DaytonaLeaseAcquisitionTimeoutError(f"Daytona {exc.operation} timed out") from None
        except BaseException:
            if context is not None and sandbox is not None:
                await self._cleanup_failed_acquisition(
                    request,
                    sandbox,
                    created_sandbox=created_sandbox,
                    deadline=deadline,
                    binding=context.persisted_binding,
                )
            raise

    async def _resolve_acquisition_context(
        self, request: LeaseRequest, *, deadline: float | None = None
    ) -> _AcquisitionContext:
        volume_id = await self._resolve_volume_id(deadline=deadline)
        expected = self._expected_execution_mount(
            volume_id=volume_id,
            workspace_id=request.workspace_id,
            session_id=request.session_id,
        )
        binding = await self._get_binding_for_workspace(request.session_id, request.workspace_id, deadline=deadline)
        if binding is not None and binding.provider_state == "fencing":
            raise DaytonaAdapterError(
                message="sandbox execution fence is not confirmed",
                cause_type="SandboxFenceUnconfirmed",
            )
        return _AcquisitionContext(expected, binding)

    async def _prepare_sandbox(
        self,
        request: LeaseRequest,
        context: _AcquisitionContext,
        *,
        deadline: float | None = None,
        force_new: bool = False,
    ) -> tuple[Any, bool]:
        sandbox = await self._reuse_bound_sandbox(request, context, deadline=deadline, force_new=force_new)
        created_sandbox = sandbox is None or force_new
        if sandbox is None:
            sandbox = await self._create_sandbox(
                volume_id=context.expected.volume_id,
                mount_path=context.expected.mount_path,
                volume_subpath=context.expected.volume_subpath,
                request=request,
                deadline=deadline,
            )
        return sandbox, created_sandbox

    async def _reuse_bound_sandbox(
        self,
        request: LeaseRequest,
        context: _AcquisitionContext,
        *,
        deadline: float | None = None,
        force_new: bool = False,
    ) -> Any | None:
        binding = context.binding
        if binding is None or not binding.sandbox_id or binding.provider_state in {"quarantined", "unrecoverable"}:
            return None
        if not binding_matches_expected(binding, context.expected):
            if (
                binding.workspace_id == request.workspace_id
                and binding.volume_subpath == workspace_volume_subpath(request.workspace_id)
                and binding.mount_path == self._volume_config.mount_path
            ):
                return await self._replace_bound_sandbox(binding, request, deadline=deadline)
            raise DaytonaAdapterError(
                message="sandbox binding does not match Session workspace scope",
                cause_type="WorkspaceMountMismatch",
            )
        if force_new:
            return await self._replace_bound_sandbox(binding, request, deadline=deadline)
        sandbox = await self._get_bound_sandbox(binding.sandbox_id, deadline=deadline)
        if sandbox is None:
            return None
        try:
            verify_sandbox_workspace_mount(sandbox, context.expected)
            verify_sandbox_spec(sandbox, self._sandbox_spec)
            sandbox = await self._ensure_running(
                sandbox,
                sandbox_state(sandbox),
                volume_id=context.expected.volume_id,
                mount_path=context.expected.mount_path,
                deadline=deadline,
            )
            verify_sandbox_workspace_mount(sandbox, context.expected)
            verify_sandbox_spec(sandbox, self._sandbox_spec)
            return sandbox
        except ProviderRequestError:
            raise
        except DaytonaAdapterError as exc:
            if exc.cause_type not in {"SandboxUnrecoverable", "SandboxSnapshotMismatch"}:
                raise
            return await self._replace_bound_sandbox(binding, request, deadline=deadline, cause=exc)

    async def _replace_bound_sandbox(
        self,
        binding: SandboxBinding,
        request: LeaseRequest,
        *,
        deadline: float | None = None,
        cause: Exception | None = None,
    ) -> Any:
        replacement = await self.replace(
            replace(binding, provider_state="unrecoverable", last_verified_at=None),
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            deadline=deadline,
        )
        replacement_id = replacement.sandbox_id
        if not replacement_id:
            err = DaytonaAdapterError(
                message="sandbox replacement did not produce a sandbox id",
                cause_type="SandboxReplaceIdentityError",
            )
            raise err from cause if cause else err
        replacement_sandbox = await self._get_bound_sandbox(replacement_id, deadline=deadline)
        if replacement_sandbox is None:
            err = DaytonaAdapterError(
                message="replacement sandbox is not retrievable",
                cause_type="SandboxUnrecoverable",
            )
            raise err from cause if cause else err
        return replacement_sandbox

    async def _get_bound_sandbox(self, sandbox_id: str, *, deadline: float | None = None) -> Any | None:
        try:
            return await _provider_call(
                self._platform.get(sandbox_id),
                deadline=deadline,
                operation="Sandbox lookup",
                owner=self._provider_tasks,
            )
        except ProviderRequestError:
            raise
        except DaytonaAdapterError:
            raise
        except _ProviderCallDeadlineError:
            raise
        except Exception as exc:
            raise map_provider_error(exc) from exc

    async def _verify_run_layout(
        self,
        sandbox: Any,
        expected: ExpectedWorkspaceMount,
        session_id: UUID,
        run_id: UUID,
        created_sandbox: bool,
        deadline: float | None = None,
    ) -> None:
        del created_sandbox

        async def verify_and_layout() -> None:
            verify_sandbox_workspace_mount(sandbox, expected)
            verify_sandbox_spec(sandbox, self._sandbox_spec)
            await verify_execution_mount(sandbox)
            if expected.session_id is not None:
                await ensure_execution_layout(sandbox, run_id=run_id)
            else:
                await ensure_volume_layout(
                    sandbox,
                    self._volume_config.paths(),
                    session_id=session_id,
                    run_id=run_id,
                )

        await _provider_call(
            verify_and_layout(),
            deadline=deadline,
            operation="Sandbox verification",
            owner=self._provider_tasks,
        )

    async def _cleanup_failed_acquisition(
        self,
        request: LeaseRequest,
        sandbox: Any,
        *,
        created_sandbox: bool,
        deadline: float | None = None,
        binding: SandboxBinding | None = None,
    ) -> bool:
        sandbox_id = _sandbox_id(sandbox)
        candidate = binding
        durable_read_failed = False
        try:
            durable_binding = await self._get_binding_for_workspace(
                request.session_id,
                request.workspace_id,
                deadline=deadline,
            )
        except Exception:
            durable_read_failed = True
            durable_binding = None
        if not durable_read_failed:
            if (
                durable_binding is None
                or durable_binding.sandbox_id != sandbox_id
                or (
                    candidate is not None
                    and (
                        candidate.sandbox_id != durable_binding.sandbox_id
                        or candidate.generation != durable_binding.generation
                    )
                )
            ):
                candidate = None
            else:
                candidate = durable_binding
        if candidate is not None and candidate.sandbox_id == sandbox_id:
            state = "quarantined" if created_sandbox else "fencing"
            with contextlib.suppress(BaseException):
                fenced = await _provider_call(
                    self._bindings.upsert(replace(candidate, provider_state=state, last_verified_at=None)),
                    deadline=deadline,
                    operation="Failed Sandbox fencing persistence",
                    owner=self._provider_tasks,
                )
                self._observe_binding(fenced)

        interpreter: DaytonaCodeInterpreter | None = None
        with contextlib.suppress(BaseException):
            interpreter = _build_interpreter_factory(
                sandbox,
                loop=asyncio.get_running_loop(),
                dispatcher=self._dispatcher,
                execution_output_cap=self._execution_output_cap,
            )

        cleanup = SandboxResource(
            owner=self,
            sandbox=sandbox,
            sandbox_id=sandbox_id,
            platform=self._platform,
            interpreter=interpreter,
            role="root" if created_sandbox else "fence",
            timeout_s=30.0,
        )
        try:
            receipt = await cleanup.aclose(deadline=deadline)
            if not receipt:
                with contextlib.suppress(BaseException):
                    await cleanup.wait_ownership()
            return receipt
        except BaseException:
            with contextlib.suppress(BaseException):
                await cleanup.wait_ownership()
            return False

    async def _persist_binding_and_build_lease(
        self,
        request: LeaseRequest,
        run_id: UUID,
        expected: ExpectedWorkspaceMount,
        sandbox: Any,
        created_sandbox: bool,
        deadline: float | None = None,
        context: _AcquisitionContext | None = None,
    ) -> InterpreterLease:
        session_id = request.session_id
        sid = _sandbox_id(sandbox)
        prior_binding = await self._get_binding_for_workspace(session_id, request.workspace_id, deadline=deadline)
        if prior_binding is None:
            binding_generation = 1
        elif (
            prior_binding.sandbox_id == sid
            and prior_binding.provider_state == "running"
            and self.is_binding_current(
                session_id=session_id,
                workspace_id=request.workspace_id,
                sandbox_id=sid,
                generation=prior_binding.generation,
            )
        ):
            binding_generation = prior_binding.generation
        else:
            binding_generation = prior_binding.generation + 1
        candidate = SandboxBinding(
            session_id=session_id,
            sandbox_id=sid,
            workspace_id=request.workspace_id,
            volume_id=expected.volume_id,
            volume_subpath=expected.volume_subpath,
            mount_path=expected.mount_path,
            provider_state="running",
            last_verified_at=datetime.now(UTC),
            generation=binding_generation,
        )
        is_replacement = prior_binding is not None and prior_binding.sandbox_id != sid
        persist = (
            self._bindings.replace_with_next_generation(candidate)
            if is_replacement
            else self._bindings.upsert(candidate)
        )
        persisted = await _provider_call(
            persist,
            deadline=deadline,
            operation="Sandbox binding persistence",
            owner=self._provider_tasks,
        )
        self._observe_binding(persisted)
        if context is not None:
            context.persisted_binding = persisted
        binding_generation = persisted.generation
        interpreter = _build_interpreter_factory(
            sandbox,
            loop=asyncio.get_running_loop(),
            dispatcher=self._dispatcher,
            execution_output_cap=self._execution_output_cap,
        )
        return InterpreterLease(
            sandbox_id=sid,
            interpreter_id=f"interp-{sid}-{uuid4().hex[:8]}",
            volume_id=expected.volume_id,
            mount_path=expected.mount_path,
            volume_subpath=expected.volume_subpath,
            interpreter=interpreter,
            sandbox=sandbox,
            session_id=str(session_id),
            user_id=str(request.user_id),
            run_id=str(run_id),
            workspace_id=str(request.workspace_id),
            created_sandbox=created_sandbox,
            binding_generation=binding_generation,
        )

    def _start_release_task(self, lease: InterpreterLease) -> asyncio.Task[None]:
        task = lease._release_task
        if task is not None and not task.done():
            return task
        if task is not None and lease.closed:
            return task
        release_task = asyncio.create_task(
            asyncio.to_thread(lease.release),
            name="fleet-daytona-interpreter-release",
        )
        lease._release_task = release_task
        _retain_provider_task(release_task, self._provider_tasks)
        release_task.add_done_callback(lambda task: self._settled_release_task(lease, task))
        return release_task

    async def _release_interpreter(self, lease: InterpreterLease) -> None:
        release_task = self._start_release_task(lease)
        try:
            await asyncio.shield(release_task)
        except Exception as exc:
            mapped = map_daytona_sdk_error(exc)
            if mapped is exc:
                raise
            raise mapped from exc

    async def release(self, lease: InterpreterLease) -> None:
        """Release an interpreter lease and perform required Sandbox containment.

        Closed leases with retired provider resources are unchanged. Leases
        awaiting containment, or requiring Sandbox deletion, complete that
        cleanup before returning. Otherwise, shut down the interpreter; failures
        propagate, with Daytona SDK errors mapped to Fleet errors. When ownership
        IDs are complete, retain failed cleanup for later release or ``aclose``.
        Cancellation of the caller does not cancel an active interpreter shutdown.

        Containment and finalization errors propagate; a local shutdown failure
        encountered during containment is suppressed once containment succeeds.
        Missing ownership IDs for required Sandbox deletion raise ``RuntimeError``;
        malformed ownership UUIDs raise ``ValueError`` when containment is arranged.
        """
        if lease.closed and lease._provider_retired:
            # Confirmed provider retirement is terminal. In particular, do
            # not reuse a failed local release task after quarantine has
            # already contained its remote resource.
            return
        unpublished = self._late_owners.get(id(lease))
        if unpublished is not None and unpublished.unpublished:
            await self._finish_unpublished_lease(unpublished)
            return
        if lease.requires_sandbox_deletion and not lease._provider_retired:
            if lease.session_id is None or lease.workspace_id is None or lease.run_id is None or lease.user_id is None:
                raise RuntimeError("native sandbox retirement requires complete lease ownership")
            await self.release_and_quarantine(
                lease,
                LeaseRequest(
                    session_id=UUID(lease.session_id),
                    workspace_id=UUID(lease.workspace_id),
                    user_id=UUID(lease.user_id),
                    run_id=UUID(lease.run_id),
                ),
            )
            return
        try:
            await self._release_interpreter(lease)
        except BaseException:
            if lease.session_id is None or lease.workspace_id is None or lease.run_id is None or lease.user_id is None:
                raise
            # A failed broker/interpreter shutdown still owns a live remote
            # resource.  Retain it behind the existing durable fencing and
            # quarantine path instead of merely logging a failed task.  Keep
            # the original failure visible to the active caller; ``aclose``
            # owns the bounded retry and final provider retirement.
            self._retain_unpublished_lease(
                lease,
                LeaseRequest(
                    session_id=UUID(lease.session_id),
                    workspace_id=UUID(lease.workspace_id),
                    user_id=UUID(lease.user_id),
                    run_id=UUID(lease.run_id),
                ),
            )
            raise

    async def _finish_unpublished_lease(
        self,
        owner: _LateOwner,
        *,
        deadline: float | None = None,
    ) -> None:
        async with owner.cleanup_lock:
            lease = owner.lease
            assert lease is not None
            if lease._provider_retired:
                self._late_owners.pop(id(lease), None)
                return
            lease._defer_owner_release = True
            lease._defer_idle_cleanup = True
            release_error: BaseException | None = None
            prior_task = lease._release_task
            prior_release_failed = (
                prior_task is not None
                and prior_task.done()
                and not prior_task.cancelled()
                and prior_task.exception() is not None
            )
            if not lease.closed and not prior_release_failed:
                try:
                    await self._release_interpreter(lease)
                except BaseException as exc:
                    release_error = exc
            # Provider retirement is still required after a broker shutdown
            # failure. It is the containment fallback for a lease whose local
            # release could not be confirmed.
            await self._quarantine(lease, owner.request, deadline=deadline)
            if release_error is not None or prior_release_failed:
                # Provider retirement has contained the failed local release.
                # Mark the lease terminal so future shutdown retries cannot
                # reopen a broker that no longer has a remote owner.
                lease._state = LeaseState.CLOSED

            callback = lease._on_release
            if not owner.callback_settled:
                if owner.callback_started:
                    raise RuntimeError("unpublished lease finalization remains unresolved")
                owner.callback_started = True
                try:
                    if callback is not None:
                        callback()
                except BaseException:
                    raise
                owner.callback_settled = True
            lease._provider_retired = True
            lease._defer_owner_release = False
            lease._defer_idle_cleanup = False
            self._late_owners.pop(id(lease), None)
            if release_error is not None:
                logger.info(
                    "Daytona interpreter release was contained by sandbox retirement",
                    extra={"sandbox_id": lease.sandbox_id, "error_type": type(release_error).__name__},
                )

    async def release_and_quarantine(
        self,
        lease: InterpreterLease,
        request: LeaseRequest,
        *,
        deadline: float | None = None,
    ) -> None:
        owner = self._retain_unpublished_lease(lease, request)
        await self._finish_unpublished_lease(owner, deadline=deadline)

    def _retain_unpublished_lease(self, lease: InterpreterLease, request: LeaseRequest) -> _LateOwner:
        """Keep failed interpreter release attached to durable cleanup ownership."""
        owner = self._late_owners.get(id(lease))
        if owner is None or not owner.unpublished:
            owner = _LateOwner(
                request=request,
                run_id=request.run_id or UUID(int=0),
                lease=lease,
                unpublished=True,
            )
            self._late_owners[id(lease)] = owner
        else:
            owner.request = request
        return owner

    async def quarantine(
        self,
        lease: InterpreterLease,
        request: LeaseRequest,
        *,
        deadline: float | None = None,
    ) -> None:
        await self._quarantine(lease, request, deadline=deadline)

    def _settled_release_task(self, lease: InterpreterLease, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except BaseException as exc:
            sandbox_id = re.sub(r"[^A-Za-z0-9_-]", "", str(lease.sandbox_id))[:64] or "unknown"
            logger.warning(
                "Daytona interpreter release failed sandbox_id=%s error_type=%s",
                sandbox_id,
                type(exc).__name__,
                extra={"sandbox_id": sandbox_id, "error_type": type(exc).__name__},
            )
            return
        if lease._release_task is task:
            lease._release_task = None
        if lease._defer_idle_cleanup or lease._provider_retired:
            return
        if self._idle_stop_seconds is None or lease.session_id is None:
            return
        session_id = UUID(lease.session_id)
        workspace_id = UUID(lease.workspace_id) if lease.workspace_id else UUID(int=0)
        idle_key = self._idle_key(session_id, workspace_id)
        self._request_cancel_idle_stop(session_id, workspace_id=workspace_id)
        idle_task = asyncio.create_task(
            self._stop_after_idle(
                session_id=session_id,
                sandbox_id=lease.sandbox_id,
                workspace_id=lease.workspace_id,
                delay=self._idle_stop_seconds,
            ),
            name="fleet-daytona-idle-stop",
        )
        self._idle_tasks[idle_key] = idle_task
        idle_task.add_done_callback(lambda completed, key=idle_key: self._forget_idle_task(key, completed))

    @staticmethod
    def _idle_key(session_id: UUID, workspace_id: UUID | None) -> tuple[UUID, UUID]:
        return (workspace_id or UUID(int=0), session_id)

    def _find_idle_task(
        self,
        session_id: UUID,
        workspace_id: UUID | None,
    ) -> tuple[tuple[UUID, UUID], asyncio.Task[None]] | None:
        if workspace_id is not None:
            key = self._idle_key(session_id, workspace_id)
            task = self._idle_tasks.get(key)
            return (key, task) if task is not None else None
        matches = [(key, task) for key, task in self._idle_tasks.items() if key[1] == session_id]
        return matches[0] if len(matches) == 1 else None

    async def _cancel_idle_stop(
        self,
        session_id: UUID,
        *,
        workspace_id: UUID | None = None,
        deadline: float | None = None,
    ) -> None:
        found = self._find_idle_task(session_id, workspace_id)
        if found is None:
            return
        key, task = found
        task.cancel()
        try:
            if deadline is None:
                await asyncio.shield(task)
            else:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError
                await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
        except TimeoutError:
            raise DaytonaLeaseAcquisitionTimeoutError("Daytona idle-stop cleanup timed out") from None
        except asyncio.CancelledError:
            if task.cancelled():
                self._forget_idle_task(key, task)
                return
            raise

    def _request_cancel_idle_stop(self, session_id: UUID, *, workspace_id: UUID | None = None) -> None:
        found = self._find_idle_task(session_id, workspace_id)
        if found is not None:
            found[1].cancel()

    def _forget_idle_task(self, key: tuple[UUID, UUID], task: asyncio.Task[None]) -> None:
        if self._idle_tasks.get(key) is task:
            self._idle_tasks.pop(key, None)

    async def _stop_after_idle(
        self,
        *,
        session_id: UUID,
        sandbox_id: str,
        workspace_id: str | None,
        delay: float,
    ) -> None:
        await asyncio.sleep(delay)
        workspace_scope = UUID(workspace_id) if workspace_id is not None else None
        if self._idle_stop_blocked(session_id, workspace_scope):
            return
        if workspace_id is not None:
            assert workspace_scope is not None
            binding = await self._get_binding_for_workspace(session_id, workspace_scope)
        else:
            binding = await _provider_call(
                self._bindings.get(session_id),
                deadline=None,
                operation="Idle Sandbox binding lookup",
                owner=self._provider_tasks,
            )
        if binding is None or binding.sandbox_id != sandbox_id or binding.provider_state != "running":
            return
        sandbox = await self._get_bound_sandbox(sandbox_id)
        if sandbox is None or self._idle_stop_blocked(session_id, workspace_scope):
            return

        stop_task = asyncio.create_task(self._platform.stop(sandbox_id))
        _retain_provider_task(stop_task, self._provider_tasks)
        try:
            await asyncio.shield(stop_task)
        except asyncio.CancelledError:
            await asyncio.shield(stop_task)
            raise
        if self._idle_stop_blocked(session_id, workspace_scope):
            return
        if workspace_id is not None:
            assert workspace_scope is not None
            latest = await self._get_binding_for_workspace(session_id, workspace_scope)
        else:
            latest = await _provider_call(
                self._bindings.get(session_id),
                deadline=None,
                operation="Idle Sandbox binding lookup",
                owner=self._provider_tasks,
            )
        if latest is None or latest.sandbox_id != sandbox_id or latest.provider_state != "running":
            return
        self.revoke_binding(
            session_id=session_id,
            workspace_id=workspace_scope or latest.workspace_id,
            sandbox_id=sandbox_id,
            generation=latest.generation,
        )
        update = asyncio.ensure_future(
            self._bindings.upsert(
                replace(
                    latest,
                    provider_state="stopped",
                    last_verified_at=datetime.now(UTC),
                    generation=latest.generation + 1,
                )
            )
        )
        _retain_provider_task(update, self._provider_tasks)
        try:
            persisted = await asyncio.shield(update)
            self._observe_binding(persisted)
        except asyncio.CancelledError:
            await OwnedEffect.from_task(update).settle()
            raise

    async def _drain_provider_ownership(self, *, drain_seconds: float = 30.0) -> bool:
        if drain_seconds < 0:
            raise ValueError("drain_seconds must be non-negative")
        deadline = asyncio.get_running_loop().time() + drain_seconds
        idle = tuple(self._idle_tasks.values())
        for task in idle:
            task.cancel()
        provider = tuple(self._provider_tasks)
        all_tasks = tuple(dict.fromkeys((*idle, *provider)))
        pending: set[asyncio.Future[Any]] = set()
        for task in all_tasks:
            if isinstance(task, asyncio.Future):
                pending.add(task)
            else:
                pending.add(asyncio.wrap_future(task))
        if pending:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            _, pending = await asyncio.wait(pending, timeout=remaining)
        if pending:
            return False

        unpublished_leases = {
            id(owner.lease) for owner in self._late_owners.values() if owner.unpublished and owner.lease is not None
        }
        known_leases = {
            id(lease): lease
            for lease in (
                *(record.root for record in self._records.values()),
                *self._late_roots.values(),
                *(owner.lease for owner in self._late_owners.values()),
            )
            if lease is not None
        }
        retry_release = [
            self._start_release_task(lease)
            for lease in known_leases.values()
            if not lease.closed and id(lease) not in unpublished_leases
        ]
        if retry_release:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            _, retry_pending = await asyncio.wait(tuple(retry_release), timeout=remaining)
            if retry_pending or any(not lease.closed for lease in known_leases.values()):
                return False

        return await self._retry_late_owners(deadline)

    def _retain_late_created_sandbox(self, task: asyncio.Future[Any]) -> None:
        async def retire_late() -> None:
            try:
                sandbox = await asyncio.shield(task)
            except BaseException:
                return
            if sandbox is None:
                return
            with contextlib.suppress(BaseException):
                await self._sandbox_retirement_lease(_sandbox_id(sandbox)).aclose()

        coroutine = retire_late()
        try:
            cleanup = asyncio.create_task(coroutine, name="fleet-daytona-late-sandbox-creation-cleanup")
        except BaseException:
            coroutine.close()
            return
        _retain_provider_task(cleanup, self._provider_tasks)

    async def _resolve_volume_id(self, *, deadline: float | None = None) -> str:
        for attempt in range(2):
            try:
                return await _provider_call(
                    get_or_create_volume_id(self._volume_client, self._volume_config),
                    deadline=deadline,
                    operation="Volume resolution",
                    owner=self._provider_tasks,
                )
            except _ProviderCallDeadlineError:
                raise
            except Exception as exc:
                mapped = map_provider_error(exc)
                if attempt == 0 and is_safe_pre_creation_retry(mapped):
                    continue
                if mapped is exc:
                    raise
                raise mapped from exc
        raise AssertionError("unreachable")

    async def replace(
        self,
        binding: SandboxBinding,
        *,
        workspace_id: UUID | None = None,
        user_id: UUID | None = None,
        deadline: float | None = None,
    ) -> SandboxBinding:
        resolved_workspace = workspace_id or binding.workspace_id
        require_non_zero_workspace_id(resolved_workspace)
        if workspace_id is not None and binding.workspace_id != workspace_id:
            raise DaytonaAdapterError(
                message="sandbox binding does not match workspace scope",
                cause_type="WorkspaceMountMismatch",
            )
        if user_id is None or user_id == UUID(int=0):
            raise DaytonaAdapterError(
                message="replace requires a real user_id (zero UUID is forbidden)",
                cause_type="SandboxReplaceIdentityError",
            )
        volume_id = binding.volume_id or await self._resolve_volume_id(deadline=deadline)
        expected = self._expected_execution_mount(
            volume_id=volume_id,
            workspace_id=resolved_workspace,
            session_id=binding.session_id,
        )
        if binding.sandbox_id:
            await self.discard_stale_root_session(resolved_workspace, binding.session_id, deadline=deadline)
            fenced = await _provider_call(
                self._bindings.upsert(replace(binding, provider_state="fencing", last_verified_at=None)),
                deadline=deadline,
                operation="Sandbox replacement fence",
                owner=self._provider_tasks,
            )
            self._observe_binding(fenced)
            retirement = self._sandbox_retirement_lease(binding.sandbox_id)
            retirement_attempted = True
            try:
                receipt = await retirement.aclose(deadline=deadline)
            except TimeoutError as exc:
                raise DaytonaAdapterError(
                    message="sandbox retirement timed out",
                    cause_type="SandboxRetirementTimeout",
                ) from exc
            if not receipt:
                if deadline is None:
                    await retirement.wait_ownership()
                else:
                    try:
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining > 0:
                            await retirement.wait_ownership(timeout=remaining)
                    except TimeoutError:
                        pass
                raise DaytonaAdapterError(
                    message="sandbox retirement was not confirmed",
                    cause_type="SandboxRetirementUnconfirmed",
                )
        else:
            retirement_attempted = False
        request = LeaseRequest(
            session_id=binding.session_id,
            user_id=user_id,
            workspace_id=resolved_workspace,
        )
        sandbox: Any | None = None
        try:
            sandbox = await self._create_sandbox(
                volume_id=expected.volume_id,
                mount_path=expected.mount_path,
                volume_subpath=expected.volume_subpath,
                request=request,
                deadline=deadline,
                settle_on_deadline=False,
            )
            verify_sandbox_workspace_mount(sandbox, expected)
            verify_sandbox_spec(sandbox, self._sandbox_spec)
            new_binding = SandboxBinding(
                session_id=binding.session_id,
                sandbox_id=_sandbox_id(sandbox),
                workspace_id=resolved_workspace,
                volume_id=expected.volume_id,
                volume_subpath=expected.volume_subpath,
                mount_path=expected.mount_path,
                provider_state="running",
                last_verified_at=datetime.now(UTC),
                generation=binding.generation + 1,
            )
            persisted = await self._bindings.replace_with_next_generation(new_binding)
            self._observe_binding(persisted)
            return persisted
        except BaseException:
            if sandbox is not None:
                with contextlib.suppress(BaseException):
                    await self._sandbox_retirement_lease(_sandbox_id(sandbox)).aclose()
            if retirement_attempted:
                with contextlib.suppress(BaseException):
                    await self._bindings.upsert(replace(binding, provider_state="quarantined", last_verified_at=None))
            raise

    async def _ensure_running(
        self,
        sandbox: Any,
        state: str,
        *,
        volume_id: str,
        mount_path: str,
        deadline: float | None = None,
    ) -> Any:
        del volume_id, mount_path
        if state == "running":
            return sandbox
        if state in {"stopped", "paused", "archived"}:
            try:
                await _provider_call(
                    self._platform.start(_sandbox_id(sandbox)),
                    deadline=deadline,
                    operation="Sandbox start",
                    owner=self._provider_tasks,
                )
                refreshed = await _provider_call(
                    self._platform.get(_sandbox_id(sandbox)),
                    deadline=deadline,
                    operation="Sandbox lookup",
                    owner=self._provider_tasks,
                )
                return refreshed or sandbox
            except _ProviderCallDeadlineError:
                raise
            except Exception as exc:
                raise map_provider_error(exc) from exc
        raise DaytonaAdapterError(
            message=f"sandbox unusable in state {state}",
            cause_type="SandboxUnrecoverable",
        )

    async def _create_sandbox(
        self,
        *,
        volume_id: str,
        mount_path: str,
        volume_subpath: str,
        request: LeaseRequest,
        deadline: float | None = None,
        settle_on_deadline: bool = True,
    ) -> Any:
        expected = ExpectedWorkspaceMount(
            volume_id=volume_id,
            volume_subpath=volume_subpath,
            mount_path=mount_path,
            workspace_id=request.workspace_id,
            session_id=request.session_id if mount_path == SESSION_WORKSPACE_MOUNT_PATH else None,
        )
        try:
            return await _provider_call(
                _create_daytona_sandbox(
                    self._platform,
                    expected,
                    labels={
                        "session_id": str(request.session_id),
                        "user_id": str(request.user_id),
                        "workspace_id": str(request.workspace_id),
                        "fleet_package": "fleet_rlm",
                        "volume_subpath": expected.volume_subpath,
                    },
                    ephemeral=False,
                ),
                deadline=deadline,
                operation="Sandbox creation",
                owner=self._provider_tasks,
            )
        except _ProviderCallDeadlineError as exc:
            if settle_on_deadline:
                return await _settle_provider_task(exc.task)
            self._retain_late_created_sandbox(exc.task)
            raise DaytonaLeaseAcquisitionTimeoutError(f"Daytona {exc.operation} timed out") from None
