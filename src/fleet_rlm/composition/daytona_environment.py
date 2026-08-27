"""Daytona composition environment inventory and Run preparation adapter."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from functools import partial
from threading import Lock
from typing import Any
from uuid import UUID

from fleet_rlm.attachments.models import (
    PreparedAttachments,
)
from fleet_rlm.chat.capability_preparation import (
    PreparedHostCapabilities,
    prepare_host_capabilities,
)
from fleet_rlm.chat.post_commit_memory import OwnedPostCommitMemoryPromotion
from fleet_rlm.chat.preparation import (
    DefaultRunPreparer,
    RunEnvironment,
    RunPreparationTimeoutError,
    RunPreparationUnavailableError,
    claim_history_records,
)
from fleet_rlm.chat.run_lifecycle import ClaimedRun
from fleet_rlm.composition.common import recursive_rlm_options
from fleet_rlm.config import Settings, load_runtime_settings
from fleet_rlm.daytona._lease import RootSessionLease
from fleet_rlm.daytona.broker import SyncBridgeDispatcher, sync_sandbox
from fleet_rlm.daytona.errors import is_sandbox_not_found
from fleet_rlm.daytona.platform import (
    LiveDaytonaPlatform,
    LiveDaytonaVolumeClient,
    build_daytona_client,
)
from fleet_rlm.daytona.provisioning import (
    DaytonaSandboxSpec,
    sandbox_spec_from_settings,
    volume_config_from_settings,
)
from fleet_rlm.daytona.recursive_child_runtime import build_child_runtime_factory
from fleet_rlm.daytona.runtime import DaytonaRuntime, RootSessionSpec
from fleet_rlm.daytona.sandbox_lease import has_pending_lease_ownership, wait_lease_ownership
from fleet_rlm.daytona.session_manager import (
    DEFAULT_IDLE_STOP_SECONDS,
    BindingStoreLike,
    DaytonaAdmission,
    DaytonaAdmissionTimeoutError,
    DaytonaLeaseAcquisitionTimeoutError,
    DaytonaSessionManager,
    LeaseRequest,
)
from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
from fleet_rlm.rlm.runtime import RLMExecutionSpec
from fleet_rlm.rlm.session_runtime import SessionKey, SessionRLMRegistry
from fleet_rlm.sessions.history import to_canonical_history_records
from fleet_rlm.sessions.history_transport import CommittedSessionHistory
from fleet_rlm.skills.catalog import SkillCatalog
from fleet_rlm.workspace.memory import MemoryCandidateCollector, build_memory_promotion_intents
from fleet_rlm.workspace.models import DAYTONA_WORKSPACE_CAPABILITY, WORKSPACE_MEMORY_INJECTION_TAIL_BYTES
from fleet_rlm.workspace.paths import VolumePaths, volume_paths_from_settings
from fleet_rlm.workspace.storage import (
    AgentAsyncVolumeStorage,
    AgentStorageSession,
    AgentVolumeStorage,
    VolumeFSCacheState,
    WorkspaceMemoryStorage,
)

logger = logging.getLogger(__name__)

_LATE_LOOKUP_OWNERS: dict[int, tuple[asyncio.Task[Any], Any]] = {}
_RESOURCE_CLEANUP_OWNERS: set[tuple[asyncio.Future[Any], Any, str]] = set()
_CLIENT_CLOSE_OWNERS: set[tuple[asyncio.Future[Any], Any]] = set()
# Keep a resident environment provider alive while it owns a root, an
# acquisition, or a late lookup. This prevents loop-bound root leases from
# disappearing before the composition owner has fenced the Daytona client.
_ENVIRONMENT_OWNERS: dict[int, Any] = {}


def has_pending_resource_cleanup() -> bool:
    """Return whether any tracked resource deletion still owns provider work."""
    return (
        any(not task.done() for task, _owner, _sandbox_id in _RESOURCE_CLEANUP_OWNERS)
        or any(not task.done() for task, _owner in _CLIENT_CLOSE_OWNERS)
        or bool(_ENVIRONMENT_OWNERS)
    )


async def wait_resource_cleanup(*, timeout: float | None = None) -> bool:
    """Wait for tracked resource deletion without cancelling late provider work."""
    tasks = tuple(task for task, _owner, _sandbox_id in _RESOURCE_CLEANUP_OWNERS if not task.done()) + tuple(
        task for task, _owner in _CLIENT_CLOSE_OWNERS if not task.done()
    )
    if not tasks:
        return True
    current_loop = asyncio.get_running_loop()
    if any(task.get_loop() is not current_loop for task in tasks):
        return False
    if timeout is None:
        await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)
    else:
        _, pending = await asyncio.wait(tasks, timeout=max(0.0, timeout))
        if pending:
            return False
    return not has_pending_resource_cleanup()


def build_committed_session_history_for_claim(claim: ClaimedRun) -> CommittedSessionHistory:
    """Materialize the canonical ``CommittedSessionHistory`` for one claimed checkpoint.

    The Daytona broker cannot inject a raw ``dspy.History`` Pydantic value
    into a Sandbox. The Daytona composition therefore projects the claimed
    Session checkpoint to the same canonical ``{"request", "answer"}``
    records consumed by :func:`to_dspy_history` and wraps them in the
    P43.7 :class:`CommittedSessionHistory` transport so the interpreter
    can reconstruct the conversation inside the Sandbox.

    The records used here are the EXACT canonical records produced by
    :func:`to_canonical_history_records`; failed, cancelled, timed-out, or
    otherwise uncommitted Turns are excluded by the canonical factory.
    The ``ClaimedRun`` carries the durable ``SessionHistory`` checkpoint. It
    may include bounded failure tombstones for audit/retry surfaces, but the
    shared typed projection excludes those using their attached committed
    result metadata and never bypasses the claim.

    The in-process composition stays on :class:`dspy.History` (see
    :func:`fleet_rlm.chat.run_preparation.build_dspy_history_for_claim`);
    this Dayona helper exists to keep the broker able to inject the value
    while preserving the canonical record contract.
    """
    committed_turns, user_requests = claim_history_records(claim)
    records = to_canonical_history_records(committed_turns, user_requests=user_requests)
    return CommittedSessionHistory(records)


def _promote_memory_candidates(
    store: Any,
    candidates: tuple[Any, ...],
    *,
    allowed_categories: tuple[str, ...],
) -> Any:
    """
    Promote memory candidates through the configured memory store.

    Parameters:
        candidates (tuple[Any, ...]): Memory candidates to promote.
        allowed_categories (tuple[str, ...]): Candidate categories eligible for promotion.

    Returns:
        MemoryCandidatePromotionResult: Counts and reasons describing the promotion outcome.
    """
    from fleet_rlm.workspace.memory import MemoryCandidatePromotionResult, promote_memory_candidates

    if store is None:
        result = MemoryCandidatePromotionResult(
            proposed_count=len(candidates),
            reasons=("store_unavailable",) if candidates else (),
        )
    else:
        result = promote_memory_candidates(
            store=store,
            candidates=candidates,
            allowed_categories=allowed_categories,
        )
    if candidates and (result.promoted_count or result.duplicate_count or result.dropped_count or result.failure_count):
        logger.info(
            "Memory Candidate promotion outcome promoted=%d duplicates=%d dropped=%d failed=%d reasons=%s",
            result.promoted_count,
            result.duplicate_count,
            result.dropped_count,
            result.failure_count,
            ",".join(result.reasons) or "-",
        )
    return result


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    """Consumes a completed task's result while suppressing cancellation and task exceptions."""
    if task.cancelled():
        return
    with contextlib.suppress(BaseException):
        task.result()


class LivePreparedCapabilities(PreparedHostCapabilities):
    """Run-bound Skill/Attachment tools and their typed public ledgers."""

    def __init__(
        self,
        spec: RLMExecutionSpec,
        *,
        files: Any,
        skills: Any,
        artifacts: Any | None = None,
        preparation_notices: tuple[Any, ...] = (),
        workspace_memory_digest: str = "",
        memory_candidates: MemoryCandidateCollector | None = None,
    ) -> None:
        super().__init__(
            spec,
            files=files,
            skills=skills,
            close_files=True,
            artifact_candidates=True,
            artifacts=artifacts,
            preparation_notices=preparation_notices,
            memory_candidates=memory_candidates,
        )
        if (
            not isinstance(workspace_memory_digest, str)
            or len(workspace_memory_digest.encode("utf-8")) > WORKSPACE_MEMORY_INJECTION_TAIL_BYTES
        ):
            workspace_memory_digest = ""
        self.workspace_memory_digest = workspace_memory_digest


# Compatibility alias for provider-local callers.  The lifecycle state machine
# is now the public/private ``RootSessionLease`` primitive in ``daytona._lease``.
_ResidentRootLease = RootSessionLease


class _DaytonaRunSink:
    def __init__(
        self,
        sandbox: Any,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        dispatcher: SyncBridgeDispatcher | None = None,
        paths: VolumePaths,
    ) -> None:
        self._sandbox = sandbox
        mount_path = str(paths.mount_path)
        # Both adapters view the same sandbox and mount; share one cache
        # coordinator so mutations through either adapter invalidate both.
        cache_state = VolumeFSCacheState()
        self._files = AgentAsyncVolumeStorage(sandbox, mount_path=mount_path, cache_state=cache_state)
        sync_backend = sync_sandbox(sandbox, loop, dispatcher) if loop is not None else None
        self.sandbox = sync_backend
        self.volume_fs = (
            AgentVolumeStorage(sync_backend, mount_path=mount_path, cache_state=cache_state)
            if sync_backend is not None
            else None
        )
        self._paths = paths

    def result_path(self, session_id: UUID, run_id: UUID) -> str:
        return str(self._paths.run_result_path(session_id, run_id))

    async def read(self, location: str, *, max_bytes: int) -> bytes:
        value = await self._files.read_bytes(location)
        if len(value) > max_bytes:
            raise ValueError("value exceeds read bound")
        return value

    async def write(self, location: str, data: bytes) -> None:
        await self._files.write_bytes(location, data)

    async def remove(self, location: str) -> None:
        await self._files.remove(location)

    async def write_private(self, logical_path: str, data: bytes) -> None:
        await self.write(logical_path, data)

    async def remove_private(self, logical_path: str) -> None:
        await self.remove(logical_path)


@dataclass(slots=True)
class _DaytonaEnvironmentProvider:
    resources: DaytonaRuntimeResources
    settings: Settings
    session_runtime_registry: SessionRLMRegistry | None = None
    _resident_root_leases: dict[tuple[UUID, UUID], _ResidentRootLease] = field(default_factory=dict, init=False)
    _resident_context_keys: dict[tuple[UUID, UUID], tuple[tuple[str, ...], tuple[tuple[str, str], ...], str | None]] = (
        field(default_factory=dict, init=False)
    )
    _resident_root_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    # Serialize root replacement and shutdown without holding the registry
    # lock across provider callbacks.
    _resident_root_transition_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    # Preparation ownership spans environment acquisition through prepared
    # cleanup.  It prevents a later context/attachment rotation from closing
    # a root while the earlier Turn is waiting to enter the RLM worker lane.
    _preparation_gates: dict[tuple[UUID, UUID], asyncio.Lock] = field(default_factory=dict, init=False)
    _acquisition_tasks: set[asyncio.Task[Any]] = field(default_factory=set, init=False, repr=False)
    _late_lookup_tasks: set[asyncio.Task[Any]] = field(default_factory=set, init=False, repr=False)
    _accepting_acquisitions: bool = field(default=True, init=False, repr=False)

    @property
    def has_pending_acquisitions(self) -> bool:
        """Whether environment acquisition still owns provider work."""
        return bool(self._acquisition_tasks or self._late_lookup_tasks)

    def _retain_environment_owner(self) -> None:
        """Keep this provider alive across caller/lifespan ownership changes."""
        _ENVIRONMENT_OWNERS[id(self)] = self

    def _maybe_release_environment_owner(self) -> None:
        """Drop process ownership only after every root/acquisition is gone."""
        if self._resident_root_leases or self._acquisition_tasks or self._late_lookup_tasks:
            return
        if _ENVIRONMENT_OWNERS.get(id(self)) is self:
            _ENVIRONMENT_OWNERS.pop(id(self), None)

    def _preparation_gate(self, key: tuple[UUID, UUID]) -> asyncio.Lock:
        gate = self._preparation_gates.get(key)
        if gate is None:
            gate = asyncio.Lock()
            self._preparation_gates[key] = gate
        return gate

    def _prune_preparation_gate(self, key: tuple[UUID, UUID]) -> None:
        """Drop an idle Session preparation gate once no root remains."""
        if key in self._resident_root_leases:
            return
        gate = self._preparation_gates.get(key)
        if gate is None or gate.locked():
            return
        waiters = getattr(gate, "_waiters", ()) or ()
        if any(not waiter.cancelled() for waiter in waiters):
            return
        self._preparation_gates.pop(key, None)

    @staticmethod
    def _context_key(
        run: ClaimedRun,
    ) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...], str | None]:
        """Return selectors that identify the immutable manifest bound to a root.

        Attachment staging is Run-scoped, so even the same durable Attachment
        IDs receive different manifest paths on the next Run.  The Daytona
        interpreter cannot replace a bound manifest; include the Run ID while
        attachments are present so the provider rotates the root before the
        next context is prepared.
        """
        attachment_ids = tuple(str(attachment_id) for attachment_id in run.input.attachment_ids)
        return (
            attachment_ids,
            tuple((str(selection.id), str(selection.expected_version)) for selection in run.input.skill_selections),
            # Attachment staging paths are Run-scoped.  Include the Run ID
            # whenever an attachment manifest exists; otherwise the provider
            # could reuse a root whose interpreter is permanently bound to the
            # prior Run's path and the next Runner rotation would inherit a
            # closed interpreter.
            str(run.run_id) if attachment_ids else None,
        )

    async def _remove_resident_root(self, key: tuple[UUID, UUID], owner: _ResidentRootLease) -> None:
        """Remove one exact provider root after successful cleanup."""
        async with self._resident_root_lock:
            if self._resident_root_leases.get(key) is owner:
                self._resident_root_leases.pop(key, None)
                self._resident_context_keys.pop(key, None)
                self._prune_preparation_gate(key)
                self._maybe_release_environment_owner()

    async def _on_root_closed(self, owner: _ResidentRootLease) -> None:
        """Remove a directly-owned root using its legacy UUID key."""
        key = owner.key
        if not isinstance(key, tuple) or len(key) != 2:
            return
        await self._remove_resident_root(key, owner)

    def _bind_runtime_root(self, key: tuple[UUID, UUID], owner: _ResidentRootLease) -> None:
        """Chain provider-map cleanup onto the public runtime callback once."""
        if getattr(owner, "_environment_provider_owner", None) is self:
            return
        previous = owner.on_closed

        async def chained(closed: _ResidentRootLease) -> None:
            if previous is not None:
                result = previous(closed)
                if inspect.isawaitable(result):
                    await result
            await self._remove_resident_root(key, closed)

        owner.on_closed = chained
        owner._environment_provider_owner = self

    def _retain_late_lookup(self, task: asyncio.Task[Any]) -> None:
        """Keep a provider lookup owned after a canceled/bounded wait."""
        if task.done():
            _consume_task_result(task)
            return
        _LATE_LOOKUP_OWNERS[id(task)] = (task, self)
        self._late_lookup_tasks.add(task)

        def settled(completed: asyncio.Task[Any]) -> None:
            _LATE_LOOKUP_OWNERS.pop(id(completed), None)
            self._late_lookup_tasks.discard(completed)
            _consume_task_result(completed)
            self._maybe_release_environment_owner()

        task.add_done_callback(settled)

    async def _finish_late_lookup(
        self,
        lookup: asyncio.Task[Any],
        owner: _ResidentRootLease,
        preparation_gate: asyncio.Lock,
        key: tuple[UUID, UUID],
        run: ClaimedRun,
    ) -> None:
        """Settle a late Sandbox lookup before releasing its root and gate."""
        try:
            await asyncio.shield(lookup)
        except asyncio.CancelledError:
            # Loop shutdown may cancel this continuation before the provider
            # identity settles. Retain the lookup itself and keep the root/gate
            # fenced; a later composition owner can retry the close safely.
            self._retain_late_lookup(lookup)
            return
        except BaseException:
            # The lookup is already owned by this continuation. Its result is
            # diagnostic-only; root retirement must still follow it.
            _consume_task_result(lookup)
        try:
            self._taint_resident_runtime(run)
            await owner.close()
        except BaseException as exc:
            # Keep the resident owner strongly reachable for the next
            # composition/runtime shutdown retry. The preparation gate can be
            # released because no RunEnvironment was returned to this Turn.
            logger.warning(
                "late Daytona Sandbox lookup cleanup failed",
                extra={"session_id": str(key[1]), "error_type": type(exc).__name__},
            )
        finally:
            if preparation_gate.locked():
                preparation_gate.release()
                self._prune_preparation_gate(key)
            self._maybe_release_environment_owner()

    def _defer_late_lookup_cleanup(
        self,
        lookup: asyncio.Task[Any],
        owner: _ResidentRootLease,
        preparation_gate: asyncio.Lock,
        key: tuple[UUID, UUID],
        run: ClaimedRun,
    ) -> None:
        """Transfer late lookup/root cleanup out of a canceled acquisition."""
        try:
            cleanup = asyncio.create_task(
                self._finish_late_lookup(lookup, owner, preparation_gate, key, run),
                name="fleet-daytona-late-sandbox-lookup-cleanup",
            )
        except BaseException:
            # A closing loop may reject a new task. Keep the provider lookup and
            # resident owner alive, and release only the preparation gate when
            # the already-started lookup eventually settles.
            def retain_settled_lookup(_completed: asyncio.Task[Any]) -> None:
                # With no task/loop available to close the resident root, keep
                # its preparation gate fenced rather than allowing a new Turn
                # to race the still-owned interpreter.
                _consume_task_result(_completed)

            self._retain_late_lookup(lookup)
            lookup.add_done_callback(retain_settled_lookup)
            return
        self._retain_late_lookup(cleanup)

    async def _acquire_root_lease(
        self,
        run: ClaimedRun,
        *,
        deadline: float,
    ) -> tuple[_ResidentRootLease, bool]:
        """Return the resident root lease and whether this call created it."""
        key = (run.access.workspace_id, run.session_id)
        context_key = self._context_key(run)
        # Keep registry access short and serialize transitions separately. A
        # RootSessionLease close callback removes the owner from this map, so
        # never await provider cleanup while holding ``_resident_root_lock``.
        async with self._resident_root_transition_lock:
            async with self._resident_root_lock:
                owner = self._resident_root_leases.get(key)
                reusable = (
                    owner is not None
                    and not owner.closed
                    and not owner.failed
                    and not owner.closing
                    and self._resident_context_keys.get(key) == context_key
                )
                if reusable:
                    return owner, False
                had_previous = owner is not None

            if owner is not None and self.session_runtime_registry is not None:
                self.session_runtime_registry.mark_tainted(SessionKey(workspace_id=str(key[0]), session_id=str(key[1])))

            # ``DaytonaRuntime`` is the canonical provider lifecycle boundary.
            # Keep the local map only as a preparation/sink index; admission,
            # Sandbox replacement, and root cleanup remain owned by the facade.
            runtime = getattr(self.resources, "runtime", None)
            if isinstance(runtime, DaytonaRuntime):
                runtime_owner = await runtime.acquire_root_session(
                    RootSessionSpec(
                        workspace_id=key[0],
                        session_id=key[1],
                        user_id=run.access.user_id,
                        run_id=run.run_id,
                        context_fingerprint=context_key,
                        deadline=deadline,
                        force_new=had_previous,
                    )
                )
                self._bind_runtime_root(key, runtime_owner)
                async with self._resident_root_lock:
                    if owner is not None and owner is not runtime_owner:
                        self._resident_root_leases.pop(key, None)
                        self._resident_context_keys.pop(key, None)
                    self._resident_root_leases[key] = runtime_owner
                    self._resident_context_keys[key] = context_key
                return runtime_owner, not reusable

            # Compatibility path for test/provider resources that predate the
            # public facade. Production DaytonaRuntimeResources always takes
            # the branch above.
            request = LeaseRequest(
                session_id=run.session_id,
                user_id=run.access.user_id,
                workspace_id=run.access.workspace_id,
                run_id=run.run_id,
            )
            force_new_sandbox = had_previous
            if owner is not None:
                # Remove the map entry only after release succeeds so a
                # provider failure/cancellation leaves a retryable owner.
                await owner.close(notify=False, deadline=deadline)
                async with self._resident_root_lock:
                    if self._resident_root_leases.get(key) is owner:
                        self._resident_root_leases.pop(key, None)
                        self._resident_context_keys.pop(key, None)
            acquire = self.resources.session_manager.acquire
            acquire_kwargs: dict[str, Any] = {"deadline": deadline}
            if force_new_sandbox:
                try:
                    supports_force_new = "force_new" in inspect.signature(acquire).parameters
                except (TypeError, ValueError):
                    supports_force_new = False
                if supports_force_new:
                    acquire_kwargs["force_new"] = True
            lease = await acquire(request, **acquire_kwargs)
            owner = _ResidentRootLease(
                key=key,
                lease=lease,
                release_callback=self.resources.session_manager.release,
                on_closed=self._on_root_closed,
            )
            async with self._resident_root_lock:
                self._resident_root_leases[key] = owner
                self._resident_context_keys[key] = context_key
            return owner, True

    async def aclose(self, *, drain_seconds: float = 30.0) -> bool:
        """Close provider roots only after tracked acquisitions settle."""
        if drain_seconds < 0:
            raise ValueError("drain_seconds must be non-negative")
        self._accepting_acquisitions = False
        current = asyncio.current_task()
        acquisitions = tuple(
            task for task in (*self._acquisition_tasks, *self._late_lookup_tasks) if task is not current
        )
        if acquisitions:
            _, pending = await asyncio.wait(acquisitions, timeout=drain_seconds)
            if pending:
                logger.warning(
                    "Daytona environment acquisition drain expired with %d owned job(s)",
                    len(pending),
                )
                return False
        # A bounded registry shutdown can leave a state-owned worker active.
        # Its deferred state close still owns the RetainableEnvironmentRelease
        # that ultimately closes this root; closing it here would terminate an
        # interpreter while that worker is still executing.
        if self.session_runtime_registry is not None and self.session_runtime_registry.has_deferred_closes:
            return False
        first_error: BaseException | None = None
        owner_deadline = asyncio.get_running_loop().time() + drain_seconds
        async with self._resident_root_transition_lock:
            async with self._resident_root_lock:
                owners = tuple(self._resident_root_leases.values())
            for owner in owners:
                try:
                    await owner.close(deadline=owner_deadline)
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
                else:
                    # ``owner.close`` invokes this callback on success in the
                    # normal path. Identity-checking here also handles a
                    # callback defect without discarding a retryable owner.
                    async with self._resident_root_lock:
                        if owner.closed and self._resident_root_leases.get(owner.key) is owner:
                            self._resident_root_leases.pop(owner.key, None)
                            self._resident_context_keys.pop(owner.key, None)
        if first_error is not None:
            raise first_error
        self._maybe_release_environment_owner()
        return True

    def _taint_resident_runtime(self, run: ClaimedRun) -> None:
        """Fence a resident runtime when provider setup proves its root unhealthy."""
        if self.session_runtime_registry is not None:
            self.session_runtime_registry.mark_tainted(
                SessionKey(
                    workspace_id=str(run.access.workspace_id),
                    session_id=str(run.session_id),
                )
            )

    async def acquire(self, run: ClaimedRun, *, deadline: float) -> RunEnvironment:
        """Acquire a Daytona environment while retaining Session preparation ownership."""
        if not self._accepting_acquisitions:
            raise RunPreparationUnavailableError("Turn environment is unavailable")
        self._retain_environment_owner()
        task = asyncio.current_task()
        if task is not None:
            self._acquisition_tasks.add(task)
        try:
            return await self._acquire(run, deadline=deadline)
        finally:
            if task is not None:
                self._acquisition_tasks.discard(task)
            self._maybe_release_environment_owner()

    async def _acquire(self, run: ClaimedRun, *, deadline: float) -> RunEnvironment:
        """Perform one tracked environment acquisition."""
        key = (run.access.workspace_id, run.session_id)
        preparation_gate = self._preparation_gate(key)
        gate_held = False
        owner: _ResidentRootLease | None = None
        created_root = False
        try:
            try:
                async with asyncio.timeout_at(deadline):
                    await preparation_gate.acquire()
            except TimeoutError:
                raise RunPreparationTimeoutError("Turn preparation timed out") from None
            gate_held = True
            try:
                owner, created_root = await self._acquire_root_lease(run, deadline=deadline)
            except DaytonaAdmissionTimeoutError as exc:
                raise RunPreparationUnavailableError("Turn environment is unavailable") from exc
            except DaytonaLeaseAcquisitionTimeoutError as exc:
                raise RunPreparationTimeoutError("Turn preparation timed out") from exc
            assert owner is not None
            root_owner = owner
            lease = root_owner.lease
            self.resources.track_sandbox(lease.sandbox_id)
            lookup = asyncio.create_task(self.resources.platform.get(lease.sandbox_id))
            try:
                async with asyncio.timeout_at(deadline):
                    sandbox = await asyncio.shield(lookup)
            except TimeoutError:
                if not lookup.done():
                    self._defer_late_lookup_cleanup(lookup, owner, preparation_gate, key, run)
                    owner = None
                    gate_held = False
                raise RunPreparationTimeoutError("Turn preparation timed out") from None
            except asyncio.CancelledError:
                if not lookup.done():
                    # Cancellation must return promptly. The detached
                    # continuation settles the provider lookup, retires the
                    # root, and releases the preparation gate in that order.
                    self._defer_late_lookup_cleanup(lookup, owner, preparation_gate, key, run)
                    owner = None
                    gate_held = False
                raise
            if sandbox is None:
                raise RuntimeError("acquired Sandbox is unavailable")
            from fleet_rlm.workspace.memory import build_workspace_memory_store

            paths = self.resources.volume_paths
            sink = _DaytonaRunSink(
                sandbox,
                loop=asyncio.get_running_loop(),
                dispatcher=getattr(self.resources, "dispatcher", None),
                paths=paths,
            )
            assert sink.volume_fs is not None
            memory_session = AgentStorageSession(
                sync_sandbox(sandbox, asyncio.get_running_loop(), getattr(self.resources, "dispatcher", None)),
                volume_root=str(paths.mount_path),
                root=str(paths.mount_path),
                max_file_bytes=self.settings.max_upload_bytes,
                allow_volume_root=True,
            )
            memory_storage = WorkspaceMemoryStorage(memory_session)
            memory_store = build_workspace_memory_store(
                memory_storage,
                max_upload_bytes=self.settings.max_upload_bytes,
            )
            memory_promotion = OwnedPostCommitMemoryPromotion(
                partial(
                    _promote_memory_candidates,
                    memory_store,
                    allowed_categories=self.settings.rlm_autonomous_memory_categories,
                )
            )

            def memory_intent_builder(run_id: Any, candidates: tuple[Any, ...]) -> tuple[Any, ...]:
                return build_memory_promotion_intents(
                    run_id=run_id,
                    candidates=candidates,
                    allowed_categories=self.settings.rlm_autonomous_memory_categories,
                )

            async def release_preparation() -> None:
                nonlocal gate_held
                if gate_held:
                    gate_held = False
                    preparation_gate.release()
                    self._prune_preparation_gate(key)

            async def release_root() -> None:
                # Only the first Turn owns the provider lease directly. Later
                # Turns reuse the resident root and release only their
                # preparation reservation.
                await root_owner.close()

            main_loop = asyncio.get_running_loop()
            child_runtime_factory = build_child_runtime_factory(
                loop=main_loop,
                dispatcher=getattr(self.resources, "dispatcher", None),
                platform=self.resources.platform,
                admission=self.resources.daytona_admission,
                volume_id=lease.volume_id,
                mount_path=self.resources.volume_config.mount_path,
                workspace_id=run.access.workspace_id,
                run_id=run.run_id,
                deadline=deadline,
                execution_timeout_s=self.settings.rlm_execution_timeout_s,
                execution_output_cap=self.settings.rlm_max_execution_output_chars,
                is_authorized=lambda: not run.authority.revoked,
            )

            return RunEnvironment(
                interpreter=lease.interpreter,
                attachment_sink=sink,
                artifact_sink=sink,
                # ``release`` is always the per-Turn preparation reservation.
                # The first resident state receives ``resident_release`` and
                # owns the root until its generation is retired.
                release=release_preparation,
                result_snapshot_sink=sink,
                child_runtime_factory=child_runtime_factory,
                context_mount_path=str(paths.mount_path),
                workspace_memory_store=memory_store,
                post_commit_memory_promotion=memory_promotion,
                memory_intent_builder=memory_intent_builder,
                resident_release=release_root if created_root else None,
                release_is_resident=False,
                history_transport=build_committed_session_history_for_claim(run),
            )
        except BaseException:
            # The preparation gate proves that no earlier same-Session Turn
            # can still own the resident execution lane.  A borrowed root may
            # therefore be tainted and closed safely on provider setup failure.
            if owner is not None:
                self._taint_resident_runtime(run)
                with contextlib.suppress(BaseException):
                    await asyncio.shield(owner.close(deadline=deadline))
            if gate_held:
                gate_held = False
                preparation_gate.release()
                self._prune_preparation_gate(key)
            raise


async def _prepare_memory_digest(memory_store: Any, *, request: str) -> str:
    """Return the per-Run injection digest, degrading fail-soft with diagnostics.

    User-visible behavior is unchanged: ANY preparation failure still degrades
    to no injection. The failure is classified once into a bounded, sanitized
    diagnostic so provider outages, corrupt stores, invariant violations, and
    internal defects no longer look identical to operators.
    """
    from fleet_rlm.workspace.memory import read_workspace_memory_injection_digest, record_memory_degradation

    try:
        return await asyncio.to_thread(
            read_workspace_memory_injection_digest,
            memory_store,
            request=request,
        )
    except Exception as exc:
        record_memory_degradation(exc, operation="injection_digest", fallback_outcome="no_memory_injection")
        return ""


@dataclass(slots=True)
class _LiveCapabilityPreparer:
    settings: Settings
    skill_catalog: SkillCatalog
    volume_paths: VolumePaths | None = None

    def __post_init__(self) -> None:
        if self.volume_paths is None:
            self.volume_paths = volume_paths_from_settings(self.settings)

    async def prepare(
        self,
        run: ClaimedRun,
        environment: RunEnvironment,
        attachments: PreparedAttachments,
        *,
        deadline: float,
    ) -> LivePreparedCapabilities:
        """
        Prepare the file, workspace, URL, and memory capabilities for a Run.

        Parameters:
            deadline (float): Deadline for capability preparation.

        Returns:
            LivePreparedCapabilities: Prepared capabilities and any preparation notices.
        """
        from fleet_rlm.artifacts.tools import ArtifactToolHost
        from fleet_rlm.attachments.tools import AttachmentToolHost
        from fleet_rlm.workspace.memory import WorkspaceMemoryToolHost, build_workspace_memory_store
        from fleet_rlm.workspace.projects import ProjectToolHost
        from fleet_rlm.workspace.storage import AgentStorageSession
        from fleet_rlm.workspace.url import UrlToolHost, WorkspaceUrlSourceStore
        from fleet_rlm.workspace.workspace import WorkspaceToolHost

        sink = environment.attachment_sink
        volume_fs = getattr(sink, "volume_fs", None)
        assert volume_fs is not None  # _DaytonaRunSink is always constructed with loop
        # Production sinks expose the sync bridge directly.  Keep the older
        # injected ``volume_fs.sandbox`` seam usable for deterministic callers.
        sandbox = getattr(sink, "sandbox", None) or getattr(volume_fs, "sandbox", None)
        if sandbox is None:
            raise TypeError("live capability preparation requires a Sandbox bridge")
        paths = self.volume_paths if self.volume_paths is not None else volume_paths_from_settings(self.settings)
        attachment_host = AttachmentToolHost(
            attachments=attachments.refs,
            staged_attachments=attachments.staged,
            volume_fs=volume_fs,
        )
        artifact_host = ArtifactToolHost(
            volume_fs=volume_fs,
            user_id=run.access.user_id,
            workspace_id=run.access.workspace_id,
            session_id=run.session_id,
            run_id=run.run_id,
            max_artifact_bytes=self.settings.max_artifact_bytes,
            volume_paths=paths,
        )
        session_workspace = AgentStorageSession(
            sandbox,
            volume_root=str(paths.mount_path),
            root=str(paths.session_workspace_dir(run.session_id)),
            max_file_bytes=self.settings.max_upload_bytes,
        )
        workspace_host = WorkspaceToolHost(
            session_workspace,
            max_file_bytes=self.settings.max_upload_bytes,
        )
        projects_fs = AgentStorageSession(
            sandbox,
            volume_root=str(paths.mount_path),
            root=str(paths.projects_root()),
            max_file_bytes=self.settings.max_upload_bytes,
        )
        project_host = ProjectToolHost(
            projects_fs,
            max_file_bytes=self.settings.max_upload_bytes,
        )
        url_host = UrlToolHost(
            session_id=run.session_id,
            store=WorkspaceUrlSourceStore(
                AgentStorageSession(
                    sandbox,
                    volume_root=str(paths.mount_path),
                    root=str(paths.session_workspace_dir(run.session_id)),
                    max_file_bytes=self.settings.max_url_bytes,
                )
            ),
            max_bytes=self.settings.max_url_bytes,
        )
        memory_store = getattr(environment, "workspace_memory_store", None)
        if memory_store is None:
            # Direct capability-preparation tests may provide only a minimal
            # RunEnvironment; production acquisition owns this store.
            memory_session = AgentStorageSession(
                sandbox,
                volume_root=str(paths.mount_path),
                root=str(paths.mount_path),
                max_file_bytes=self.settings.max_upload_bytes,
                allow_volume_root=True,
            )
            memory_store = build_workspace_memory_store(
                WorkspaceMemoryStorage(memory_session),
                max_upload_bytes=self.settings.max_upload_bytes,
            )
        memory_host = WorkspaceMemoryToolHost(memory_store)
        memory_candidates = None
        candidate_tools: tuple[Any, ...] = ()
        candidate_views: dict[str, Any] = {}
        if self.settings.rlm_autonomous_memory_categories:
            from fleet_rlm.workspace.memory import MemoryCandidateCollector, MemoryCandidateToolHost

            memory_candidates = MemoryCandidateCollector(
                run_id=run.run_id,
                allowed_categories=self.settings.rlm_autonomous_memory_categories,
            )
            candidate_host = MemoryCandidateToolHost(memory_candidates)
            candidate_tools = candidate_host.as_tools()
            candidate_views = dict(candidate_host.event_views())
        # Per-Run Workspace Memory injection: relevant matches first, then the
        # newest complete records. Best-effort by contract; search/storage
        # failures degrade to no injection, and search failure degrades to
        # the recency-only fallback. Every degraded operation records one
        # bounded, sanitized diagnostic at this fail-soft seam (P31).
        memory_digest = await _prepare_memory_digest(memory_store, request=run.input.text)
        attachment_tools = attachment_host.as_tools()
        artifact_tools = artifact_host.as_tools()
        workspace_tools = workspace_host.as_tools()
        project_tools = project_host.as_tools()
        memory_tools = memory_host.as_tools()
        url_tools = url_host.as_tools()
        base_views = {
            **attachment_host.event_views(),
            **artifact_host.event_views(),
            **workspace_host.event_views(),
            **project_host.event_views(),
            **memory_host.event_views(),
            **candidate_views,
            **url_host.event_views(),
        }
        spec, skill_host, notices = await prepare_host_capabilities(
            turn=run,
            skill_catalog=self.skill_catalog,
            base_tools=(
                *attachment_tools,
                *artifact_tools,
                *workspace_tools,
                *project_tools,
                *memory_tools,
                *candidate_tools,
                *url_tools,
            ),
            base_event_views=base_views,
            workspace=DAYTONA_WORKSPACE_CAPABILITY,
            workspace_fs=session_workspace,
            deadline=deadline,
        )
        return LivePreparedCapabilities(
            spec,
            files=attachment_host,
            artifacts=artifact_host,
            skills=skill_host,
            preparation_notices=notices,
            workspace_memory_digest=memory_digest,
            memory_candidates=memory_candidates,
        )


def resolve_settings(settings: Settings | None = None) -> Settings:
    """Return explicit settings or load the resolved TOML policy."""
    return settings or load_runtime_settings()


class DaytonaRuntimeResources:
    """Provider-owned Daytona clients and session lifecycle for one process."""

    def __init__(
        self,
        settings: Settings,
        *,
        bindings: BindingStoreLike,
        cleanup: Any,
        sandbox_spec: DaytonaSandboxSpec | None = None,
        max_active_leases: int,
        idle_stop_seconds: float | None = DEFAULT_IDLE_STOP_SECONDS,
        execution_output_cap: int,
        execution_timeout_s: int,
        dispatcher: SyncBridgeDispatcher | None = None,
    ) -> None:
        self.settings = resolve_settings(settings)
        self.sandbox_spec = sandbox_spec or sandbox_spec_from_settings(self.settings)
        self.client = build_daytona_client(self.settings)
        self.dispatcher = dispatcher
        self.platform = LiveDaytonaPlatform(self.client, self.sandbox_spec)
        self.volume_client = LiveDaytonaVolumeClient(self.client)
        self.volume_config = volume_config_from_settings(self.settings)
        self.volume_paths = volume_paths_from_settings(self.settings)
        self.bindings = bindings
        self.daytona_admission = DaytonaAdmission(
            max_active_leases=max_active_leases,
        )
        self.session_manager = DaytonaSessionManager(
            platform=self.platform,
            volume_client=self.volume_client,
            volume_config=self.volume_config,
            bindings=self.bindings,
            admission=self.daytona_admission,
            sandbox_spec=self.sandbox_spec,
            cleanup=cleanup,
            idle_stop_seconds=idle_stop_seconds,
            execution_output_cap=execution_output_cap,
            execution_timeout_s=execution_timeout_s,
            dispatcher=dispatcher,
        )
        # Public root/child ownership boundary.  The existing Run-preparation
        # provider remains the compatibility adapter for the richer Run sink;
        # both paths share this resource-owned manager and therefore the same
        # admission and provider cleanup guarantees.
        self.runtime = DaytonaRuntime(self)
        self._sandbox_ids: list[str] = []
        self._client_close_lock = Lock()
        self._client_close_task: asyncio.Task[Any] | None = None
        self._client_closed = False

    def track_sandbox(self, sandbox_id: str | None) -> None:
        if sandbox_id and sandbox_id not in self._sandbox_ids:
            self._sandbox_ids.append(sandbox_id)

    async def cleanup(self, *, deadline: float | None = None) -> bool:
        """Delete tracked Sandboxes with bounded, retained provider requests."""
        settled = True
        retained: list[str] = []

        async def request(value: Awaitable[Any], sandbox_id: str) -> tuple[bool, Any]:
            nonlocal settled
            task = asyncio.ensure_future(value)
            try:
                if deadline is None:
                    return True, await asyncio.shield(task)
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError
                return True, await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
            except TimeoutError:
                _RESOURCE_CLEANUP_OWNERS.add((task, self, sandbox_id))
                task.add_done_callback(lambda completed, sid=sandbox_id: self._settled_resource_cleanup(sid, completed))
                settled = False
                return False, None
            except asyncio.CancelledError:
                _RESOURCE_CLEANUP_OWNERS.add((task, self, sandbox_id))
                task.add_done_callback(lambda completed, sid=sandbox_id: self._settled_resource_cleanup(sid, completed))
                settled = False
                raise
            except Exception as exc:
                # A tracked Sandbox may already have been retired by a
                # context-rotation replacement. Its absence is successful
                # cleanup, not a retryable provider failure.
                if is_sandbox_not_found(exc):
                    return True, None
                settled = False
                return False, None

        owns = getattr(self.session_manager, "owns_sandbox", None)
        for sid in list(self._sandbox_ids):
            if callable(owns) and owns(sid):
                retained.append(sid)
                settled = False
                continue
            try:
                deleted, _ = await request(self.platform.delete(sid), sid)
                if not deleted:
                    retained.append(sid)
                    continue
                probe = getattr(self.platform, "get", None)
                if callable(probe):
                    confirmed, result = await request(probe(sid), sid)
                    if not confirmed or result is not None:
                        retained.append(sid)
            except Exception:
                # Keep failed identities for a later owner/retry rather than
                # forgetting them immediately before client disposal.
                retained.append(sid)
                settled = False
        self._sandbox_ids = retained
        return settled

    def _settled_resource_cleanup(self, sandbox_id: str, task: asyncio.Future[Any]) -> None:
        _RESOURCE_CLEANUP_OWNERS.difference_update(
            owner
            for owner in _RESOURCE_CLEANUP_OWNERS
            if owner[1] is self and owner[2] == sandbox_id and owner[0] is task
        )
        if task.cancelled():
            return
        with contextlib.suppress(BaseException):
            task.exception()

    async def _close_client(self, *, deadline: float | None) -> bool:
        """Close the Daytona client under one retained, bounded task."""
        if self._client_closed:
            return True
        with self._client_close_lock:
            task = self._client_close_task
            if task is not None and task.done():
                if not task.cancelled():
                    with contextlib.suppress(BaseException):
                        error = task.exception()
                    if error is None:
                        self._client_closed = True
                        return True
                self._client_close_task = None
                task = None
            if task is None:
                task = asyncio.create_task(self.client.close(), name="fleet-daytona-client-close")
                self._client_close_task = task
                _CLIENT_CLOSE_OWNERS.add((task, self))

                def settled(completed: asyncio.Task[Any]) -> None:
                    _CLIENT_CLOSE_OWNERS.discard((completed, self))
                    if not completed.cancelled():
                        with contextlib.suppress(BaseException):
                            error = completed.exception()
                        if error is None:
                            self._client_closed = True

                task.add_done_callback(settled)
        try:
            if deadline is None:
                await asyncio.shield(task)
            else:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return False
                await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
        except TimeoutError:
            # Keep the close task and client strongly owned for a later retry.
            return False
        except asyncio.CancelledError:
            # Caller cancellation must not cancel the client close operation.
            raise
        except BaseException:
            with self._client_close_lock:
                if self._client_close_task is task:
                    self._client_close_task = None
            raise
        else:
            self._client_closed = True
            return True

    async def adispose(self, *, drain_seconds: float = 30.0) -> bool:
        """Bound provider-owned shutdown without abandoning late ownership."""
        started = asyncio.get_running_loop().time()
        runtime_settled = await self.runtime.aclose(deadline=started + drain_seconds)
        settled = await self.session_manager.aclose(drain_seconds=drain_seconds)
        cleanup_settled = await self.cleanup(deadline=started + drain_seconds)
        pending_ownership = bool(getattr(self.session_manager, "has_pending_ownership", False))
        resource_ownership = has_pending_resource_cleanup()
        if resource_ownership:
            remaining = max(0.0, started + drain_seconds - asyncio.get_running_loop().time())
            resource_ownership = not await wait_resource_cleanup(timeout=remaining)
        lease_ownership = has_pending_lease_ownership()
        if lease_ownership:
            remaining = max(0.0, started + drain_seconds - asyncio.get_running_loop().time())
            lease_ownership = not await wait_lease_ownership(timeout=remaining)
        pending = (
            not runtime_settled
            or not settled
            or not cleanup_settled
            or pending_ownership
            or resource_ownership
            or lease_ownership
            or bool(self._sandbox_ids)
        )
        if pending:
            logger.warning(
                "Daytona provider disposal retained owned Sandbox resources",
                extra={"sandbox_count": len(self._sandbox_ids)},
            )
            return False
        return await self._close_client(deadline=started + drain_seconds)


def build_run_preparation(
    resources: DaytonaRuntimeResources,
    *,
    attachment_lifecycle: Any,
    skill_catalog: SkillCatalog,
    settings: Settings,
    models: RLMModelBundle,
    session_runtime_registry: SessionRLMRegistry | None = None,
) -> DefaultRunPreparer:
    """Compose Daytona Run preparation without mutating resource ownership."""
    options = RLMOptions(
        max_iters=settings.rlm_max_iters,
        max_llm_calls=settings.rlm_max_llm_calls,
        max_output_chars=settings.rlm_max_output_chars,
    )
    return DefaultRunPreparer(
        models=models,
        options=options,
        recursive_options=recursive_rlm_options(settings),
        attachments=attachment_lifecycle,
        environments=_DaytonaEnvironmentProvider(resources, settings, session_runtime_registry),
        capabilities=_LiveCapabilityPreparer(settings, skill_catalog, volume_paths=resources.volume_paths),
        session_runtime_registry=session_runtime_registry,
    )


__all__ = [
    "DaytonaRuntimeResources",
    "LivePreparedCapabilities",
    "_DaytonaEnvironmentProvider",
    "_DaytonaRunSink",
    "_LiveCapabilityPreparer",
    "_ResidentRootLease",
    "_prepare_memory_digest",
    "_promote_memory_candidates",
    "build_committed_session_history_for_claim",
    "build_run_preparation",
    "has_pending_resource_cleanup",
    "resolve_settings",
    "wait_resource_cleanup",
]
