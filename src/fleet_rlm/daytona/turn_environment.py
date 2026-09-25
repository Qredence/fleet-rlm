"""Daytona environment acquisition and Turn-facing capability adapters."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any
from uuid import UUID

from fleet_rlm.artifacts.reader import ArtifactReader
from fleet_rlm.attachments import (
    PreparedAttachments,
)
from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.diagnostics import environment_manifest
from fleet_rlm.daytona.interpreter import SyncBridgeDispatcher, sync_sandbox
from fleet_rlm.daytona.runtime import (
    DEFAULT_IDLE_STOP_SECONDS,
    BindingStoreLike,
    DaytonaAdmissionTimeoutError,
    DaytonaEnvironmentProfile,
    DaytonaLeaseAcquisitionTimeoutError,
    DaytonaRuntime,
    DaytonaSandboxSpec,
    RootSessionLease,
    RootSessionSpec,
    sandbox_spec_from_settings,
)
from fleet_rlm.rlm.execution import RLMExecutionSpec
from fleet_rlm.sessions.history import to_canonical_history_records
from fleet_rlm.sessions.history_transport import CommittedSessionHistory
from fleet_rlm.sessions.run_state import ClaimedRun
from fleet_rlm.skills.catalog import SkillCatalog
from fleet_rlm.turn_preparation import (
    PreparedHostCapabilities,
    RunEnvironment,
    RunPreparationTimeoutError,
    RunPreparationUnavailableError,
    claim_history_records,
    prepare_host_capabilities,
)
from fleet_rlm.turn_settlement import OwnedPostCommitMemoryPromotion
from fleet_rlm.workspace.memory import (
    MemoryCandidateCollector,
    build_memory_promotion_intents,
    prepare_turn_memory_digest,
    promote_turn_memory_candidates,
)
from fleet_rlm.workspace.models import DAYTONA_WORKSPACE_CAPABILITY, WORKSPACE_MEMORY_INJECTION_TAIL_BYTES
from fleet_rlm.workspace.paths import VolumePaths, volume_paths_from_settings
from fleet_rlm.workspace.storage import (
    AgentAsyncVolumeStorage,
    AgentVolumeStorage,
    DaytonaSandboxWorkspaceStorage,
    VolumeFSCacheState,
    WorkspaceMemoryStorage,
)

logger = logging.getLogger(__name__)


def _workspace_storage(sandbox: Any, **kwargs: Any) -> Any:
    """Bind live workspaces to Sandbox FS while retaining local deterministic doubles."""
    if getattr(sandbox, "fs", None) is not None:
        return DaytonaSandboxWorkspaceStorage(sandbox, **kwargs)
    from fleet_rlm.workspace.storage import AgentStorageSession as LocalStorage

    return LocalStorage(sandbox, **kwargs)


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
    :func:`fleet_rlm.turn_preparation.build_dspy_history_for_claim`);
    this Dayona helper exists to keep the broker able to inject the value
    while preserving the canonical record contract.
    """
    committed_turns, user_requests = claim_history_records(claim)
    records = to_canonical_history_records(committed_turns, user_requests=user_requests)
    return CommittedSessionHistory(records)


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
        await self._files.remove_bytes(location)

    async def write_private(self, logical_path: str, data: bytes) -> None:
        await self.write(logical_path, data)

    async def remove_private(self, logical_path: str) -> None:
        await self.remove(logical_path)


@dataclass(slots=True)
class _DaytonaEnvironmentProvider:
    resources: DaytonaRuntimeResources
    settings: Settings
    _acquisition_tasks: set[asyncio.Task[Any]] = field(default_factory=set, init=False, repr=False)
    _accepting_acquisitions: bool = field(default=True, init=False, repr=False)

    @property
    def has_pending_acquisitions(self) -> bool:
        """Whether environment acquisition still owns provider work."""
        return bool(self._acquisition_tasks)

    def _retain_environment_owner(self) -> None:
        """Keep this provider alive across caller/lifespan ownership changes."""
        retain = getattr(self.resources, "retain_environment_provider", None)
        if callable(retain):
            retain(self)

    def _maybe_release_environment_owner(self) -> None:
        """Drop process ownership only after every root/acquisition is gone."""
        if self._acquisition_tasks:
            return
        release = getattr(self.resources, "release_environment_provider", None)
        if callable(release):
            release(self)

    async def wait_for_session_idle(
        self,
        workspace_id: UUID,
        session_id: UUID,
        *,
        deadline: float,
    ) -> None:
        """Wait for a prepared Turn before retiring its shared Session root."""
        await self.resources.runtime.wait_for_session_idle(workspace_id, session_id, deadline=deadline)

    def _mark_provider_root_tainted(self, key: tuple[UUID, UUID]) -> None:
        """Require a fresh provider root on the next acquisition for ``key``."""
        runtime = getattr(self.resources, "runtime", None)
        if isinstance(runtime, DaytonaRuntime):
            runtime.mark_root_tainted(*key)

    def _taint_resident_runtime(self, run: ClaimedRun) -> None:
        """Fence a resident runtime when provider setup proves its root unhealthy."""
        self._mark_provider_root_tainted((run.access.workspace_id, run.session_id))

    @staticmethod
    def _context_key(
        run: ClaimedRun,
    ) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...], str | None]:
        """Return selectors that identify the immutable manifest bound to a root."""
        attachment_ids = tuple(str(attachment_id) for attachment_id in run.input.attachment_ids)
        return (
            attachment_ids,
            tuple((str(selection.id), str(selection.expected_version)) for selection in run.input.skill_selections),
            str(run.run_id) if attachment_ids else None,
        )

    async def _acquire_root_lease(
        self,
        run: ClaimedRun,
        *,
        deadline: float,
    ) -> tuple[RootSessionLease, bool]:
        """Return the runtime-owned root lease."""
        key = (run.access.workspace_id, run.session_id)
        runtime = getattr(self.resources, "runtime", None)
        if not isinstance(runtime, DaytonaRuntime):
            raise RuntimeError("Daytona runtime is required to acquire a provider root")
        owner = await runtime.acquire_root_session(
            RootSessionSpec(
                workspace_id=key[0],
                session_id=key[1],
                user_id=run.access.user_id,
                run_id=run.run_id,
                context_fingerprint=self._context_key(run),
                deadline=deadline,
            )
        )
        return owner, False

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
        key = (run.access.workspace_id, run.session_id)
        release_invocation: Callable[[], None] | None = None
        owner: RootSessionLease | None = None
        try:
            try:
                release_invocation = await self.resources.runtime.begin_root_invocation(
                    *key, run.run_id, deadline=deadline
                )
            except TimeoutError:
                raise RunPreparationTimeoutError("Turn preparation timed out") from None
            try:
                owner, _created_root = await self._acquire_root_lease(run, deadline=deadline)
            except DaytonaAdmissionTimeoutError as exc:
                raise RunPreparationUnavailableError("Turn environment is unavailable") from exc
            except (DaytonaLeaseAcquisitionTimeoutError, TimeoutError) as exc:
                raise RunPreparationTimeoutError("Turn preparation timed out") from exc
            assert owner is not None
            lease = owner.lease
            self.resources.runtime.track_sandbox(lease.sandbox_id)
            sandbox = owner.sandbox
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
            memory_session = _workspace_storage(
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
                    promote_turn_memory_candidates,
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
                if release_invocation is not None:
                    release_invocation()

            child_runtime_factory = self.resources.runtime.build_child_factory(
                volume_id=lease.volume_id,
                mount_path=self.resources.volume_config.mount_path,
                workspace_id=run.access.workspace_id,
                run_id=run.run_id,
                deadline=deadline,
                execution_timeout_s=self.settings.rlm_execution_timeout_s,
                execution_output_cap=self.settings.rlm_max_execution_output_chars,
                is_authorized=lambda: not run.authority.revoked,
                semantic_child_available=bool(getattr(self.settings, "daytona_child_snapshot", None)),
                semantic_child_fallback=True,
            )

            sandbox_spec = getattr(self.resources, "sandbox_spec", None)
            image_identity = environment_manifest(sandbox_spec).digest if sandbox_spec is not None else None
            return RunEnvironment(
                interpreter=lease.interpreter,
                attachment_sink=sink,
                artifact_sink=sink,
                release=release_preparation,
                result_snapshot_sink=sink,
                child_runtime_factory=child_runtime_factory,
                context_mount_path=str(paths.mount_path),
                workspace_memory_store=memory_store,
                post_commit_memory_promotion=memory_promotion,
                memory_intent_builder=memory_intent_builder,
                resident_release=None,
                release_is_resident=False,
                history_transport=build_committed_session_history_for_claim(run),
                mark_tainted=lambda k=key: self._mark_provider_root_tainted(k),
                async_bridge=getattr(self.resources, "dispatcher", None),
                image_identity=image_identity,
            )
        except BaseException:
            self._taint_resident_runtime(run)
            if owner is not None:
                try:
                    await asyncio.shield(owner.close(deadline=deadline))
                except BaseException as exc:
                    logger.warning(
                        "Daytona root cleanup failed on error",
                        extra={"session_id": str(key[1]), "error_type": type(exc).__name__},
                    )
            if release_invocation is not None:
                release_invocation()
            raise

    async def aclose(self, *, drain_seconds: float = 30.0) -> bool:
        """Close provider roots only after tracked acquisitions settle."""
        if drain_seconds < 0:
            raise ValueError("drain_seconds must be non-negative")
        self._accepting_acquisitions = False
        current = asyncio.current_task()
        pending_tasks = tuple(task for task in self._acquisition_tasks if task is not current and not task.done())
        if pending_tasks:
            _, pending = await asyncio.wait(pending_tasks, timeout=drain_seconds)
            if pending:
                logger.warning(
                    "Daytona environment acquisition drain expired with %d owned job(s)",
                    len(pending),
                )
                return False
        self._maybe_release_environment_owner()
        return True


@dataclass(slots=True)
class _LiveCapabilityPreparer:
    settings: Settings
    skill_catalog: SkillCatalog
    volume_paths: VolumePaths | None = None
    artifact_reader: ArtifactReader | None = None

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
        from fleet_rlm.attachments import AttachmentToolHost
        from fleet_rlm.workspace.memory import WorkspaceMemoryToolHost, build_workspace_memory_store
        from fleet_rlm.workspace.projects import ProjectToolHost
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
        session_workspace = _workspace_storage(
            sandbox,
            volume_root=str(paths.mount_path),
            root=str(paths.session_workspace_dir(run.session_id)),
            max_file_bytes=self.settings.max_upload_bytes,
        )
        workspace_host = WorkspaceToolHost(
            session_workspace,
            max_file_bytes=self.settings.max_upload_bytes,
        )
        projects_fs = _workspace_storage(
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
                _workspace_storage(
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
            memory_session = _workspace_storage(
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
        memory_digest = await prepare_turn_memory_digest(memory_store, request=run.input.text)
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
            artifact_reader=self.artifact_reader,
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
    from fleet_rlm.config.loader import load_runtime_settings

    return settings or load_runtime_settings()


class DaytonaRuntimeResources:
    """Composition wiring and provider-reference retention for one process."""

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
        self.environment_specs: dict[DaytonaEnvironmentProfile, DaytonaSandboxSpec] = {
            DaytonaEnvironmentProfile.SESSION: self.sandbox_spec,
        }
        if getattr(self.settings, "daytona_child_snapshot", None):
            self.environment_specs[DaytonaEnvironmentProfile.SEMANTIC_CHILD] = sandbox_spec_from_settings(
                self.settings,
                DaytonaEnvironmentProfile.SEMANTIC_CHILD,
            )
        # WorkspaceChild deliberately reuses the Session image and resource
        # identity, but remains an explicit profile at child-acquisition time.
        self.environment_specs[DaytonaEnvironmentProfile.WORKSPACE_CHILD] = DaytonaSandboxSpec(
            snapshot=self.sandbox_spec.snapshot,
            python_version=self.sandbox_spec.python_version,
            base_image=self.sandbox_spec.base_image,
            profile=DaytonaEnvironmentProfile.WORKSPACE_CHILD,
        )
        self.dispatcher = dispatcher
        self.volume_paths = volume_paths_from_settings(self.settings)
        self.bindings = bindings
        self.runtime = DaytonaRuntime.from_settings(
            self.settings,
            environment_specs=self.environment_specs,
            sandbox_spec=self.sandbox_spec,
            bindings=self.bindings,
            cleanup=cleanup,
            max_active_leases=max_active_leases,
            idle_stop_seconds=idle_stop_seconds,
            execution_output_cap=execution_output_cap,
            execution_timeout_s=execution_timeout_s,
            dispatcher=dispatcher,
        )
        self.volume_config = self.runtime._volume_config
        # Environment providers are composition references, not SDK owners.
        self._environment_owners: dict[int, Any] = {}

    def retain_environment_provider(self, provider: Any) -> None:
        self._environment_owners[id(provider)] = provider

    def release_environment_provider(self, provider: Any) -> None:
        if self._environment_owners.get(id(provider)) is provider:
            self._environment_owners.pop(id(provider), None)

    def has_pending_cleanup(self) -> bool:
        return bool(self._environment_owners) or self.runtime.has_pending_cleanup()

    async def wait_pending_cleanup(self, *, timeout: float | None = None) -> bool:
        settled = await self.runtime.wait_pending_cleanup(timeout=timeout)
        return settled and not self._environment_owners

    async def adispose(self, *, drain_seconds: float = 30.0) -> bool:
        if self._environment_owners:
            await self.runtime.aclose(drain_seconds=drain_seconds)
            return False
        return await self.runtime.adispose(drain_seconds=drain_seconds)


__all__ = [
    "DaytonaRuntimeResources",
    "LivePreparedCapabilities",
    "_DaytonaEnvironmentProvider",
    "_DaytonaRunSink",
    "_LiveCapabilityPreparer",
    "build_committed_session_history_for_claim",
    "resolve_settings",
]
