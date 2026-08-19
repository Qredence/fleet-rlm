"""Daytona Run environment inventory and exact Run preparation adapter."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, cast
from uuid import UUID

from fleet_rlm.chat.capability_preparation import (
    PreparedHostCapabilities,
    prepare_host_capabilities,
)
from fleet_rlm.chat.post_commit_memory import OwnedPostCommitMemoryPromotion
from fleet_rlm.chat.run_lifecycle import ClaimedRun
from fleet_rlm.chat.run_preparation import (
    DefaultRunPreparer,
    RunEnvironment,
    RunPreparationTimeoutError,
    RunPreparationUnavailableError,
)
from fleet_rlm.composition.common import recursive_rlm_options
from fleet_rlm.config import Settings, load_runtime_settings
from fleet_rlm.daytona.dspy_sync_bridge import SyncBridgeDispatcher, sync_sandbox
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
from fleet_rlm.daytona.session_manager import (
    DEFAULT_IDLE_STOP_SECONDS,
    BindingStoreLike,
    DaytonaAdmission,
    DaytonaAdmissionTimeoutError,
    DaytonaLeaseAcquisitionTimeoutError,
    DaytonaSessionManager,
    LeaseRequest,
)
from fleet_rlm.daytona.workspace_fs import AsyncDaytonaVolumeFS, DaytonaSandboxVolumeFs, VolumeFSCacheState
from fleet_rlm.files.memory_candidates import MemoryCandidateCollector, build_memory_promotion_intents
from fleet_rlm.files.memory_models import WORKSPACE_MEMORY_INJECTION_TAIL_BYTES
from fleet_rlm.files.models import (
    AttachmentAccess,
    AttachmentRun,
    PreparedAttachments,
)
from fleet_rlm.files.volume_paths import VolumePaths, volume_paths_from_settings
from fleet_rlm.files.workspace_models import DAYTONA_WORKSPACE_CAPABILITY
from fleet_rlm.rlm.context import RLMExecutionSpec
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.runtime.owned_effect import OwnedEffect
from fleet_rlm.skills.catalog import SkillCatalog

logger = logging.getLogger(__name__)


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
    from fleet_rlm.files.memory_candidates import MemoryCandidatePromotionResult, promote_memory_candidates

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
        self._files = AsyncDaytonaVolumeFS(sandbox, mount_path=mount_path, cache_state=cache_state)
        self.volume_fs = (
            DaytonaSandboxVolumeFs(
                sync_sandbox(sandbox, loop, dispatcher), mount_path=mount_path, cache_state=cache_state
            )
            if loop is not None
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

    async def acquire(self, run: ClaimedRun, *, deadline: float) -> RunEnvironment:
        """
        Acquire and configure a Daytona-backed environment for a run.

        Parameters:
            run (ClaimedRun): Run whose session, access, and identifiers determine the environment.
            deadline (float): Absolute time limit for environment acquisition and setup.

        Returns:
            RunEnvironment: Configured environment with run sinks, cleanup, memory services, and child-runtime creation.

        Raises:
            RunPreparationUnavailableError: If environment admission times out.
            RunPreparationTimeoutError: If lease acquisition or environment setup exceeds the deadline.
            RuntimeError: If the acquired sandbox is unavailable.
        """
        try:
            lease = await self.resources.session_manager.acquire(
                LeaseRequest(
                    session_id=run.session_id,
                    user_id=run.access.user_id,
                    workspace_id=run.access.workspace_id,
                    run_id=run.run_id,
                ),
                deadline=deadline,
            )
        except DaytonaAdmissionTimeoutError as exc:
            raise RunPreparationUnavailableError("Turn environment is unavailable") from exc
        except DaytonaLeaseAcquisitionTimeoutError as exc:
            raise RunPreparationTimeoutError("Turn preparation timed out") from exc
        try:
            self.resources.track_sandbox(lease.sandbox_id)
            lookup = asyncio.create_task(self.resources.platform.get(lease.sandbox_id))
            lookup_effect = OwnedEffect.from_task(lookup)
            try:
                async with asyncio.timeout_at(deadline):
                    sandbox = await asyncio.shield(lookup)
            except TimeoutError:
                if lookup.done():
                    raise
                try:
                    settled = await lookup_effect.settle()
                except BaseException:
                    lookup_effect.consume_exception()
                    cancelled = lookup_effect.caller_cancelled
                else:
                    cancelled = settled.caller_cancelled
                _consume_task_result(lookup)
                if cancelled:
                    raise asyncio.CancelledError from None
                raise RunPreparationTimeoutError("Turn preparation timed out") from None
            except asyncio.CancelledError:
                with contextlib.suppress(BaseException):
                    await lookup_effect.settle()
                _consume_task_result(lookup)
                raise
            if sandbox is None:
                raise RuntimeError("acquired Sandbox is unavailable")
            from fleet_rlm.daytona.workspace_memory import DaytonaWorkspaceMemoryStore

            paths = volume_paths_from_settings(self.settings)
            sink = _DaytonaRunSink(
                sandbox,
                loop=asyncio.get_running_loop(),
                dispatcher=getattr(self.resources, "dispatcher", None),
                paths=paths,
            )
            assert sink.volume_fs is not None
            memory_store = DaytonaWorkspaceMemoryStore(
                sink.volume_fs.sandbox,
                volume_paths=paths,
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

            async def release() -> None:
                await self.resources.session_manager.release(lease)

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
                release=release,
                result_snapshot_sink=sink,
                child_runtime_factory=child_runtime_factory,
                context_mount_path=str(paths.mount_path),
                workspace_memory_store=memory_store,
                post_commit_memory_promotion=memory_promotion,
                memory_intent_builder=memory_intent_builder,
            )
        except BaseException:
            await asyncio.shield(self.resources.session_manager.release(lease))
            raise


@dataclass(slots=True)
class _LiveAttachmentLifecycle:
    attachment_lifecycle: Any

    async def prepare_run(
        self,
        access: AttachmentAccess,
        attachment_ids: Sequence[UUID],
        run: AttachmentRun,
        sink: Any,
    ) -> PreparedAttachments:
        return await self.attachment_lifecycle.prepare_run(access, attachment_ids, run, sink)


async def _prepare_memory_digest(memory_store: Any, *, request: str) -> str:
    """Return the per-Run injection digest, degrading fail-soft with diagnostics.

    User-visible behavior is unchanged: ANY preparation failure still degrades
    to no injection. The failure is classified once into a bounded, sanitized
    diagnostic so provider outages, corrupt stores, invariant violations, and
    internal defects no longer look identical to operators.
    """
    from fleet_rlm.daytona.memory_diagnostics import record_memory_degradation
    from fleet_rlm.daytona.workspace_memory import read_workspace_memory_injection_digest

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
        from fleet_rlm.daytona.workspace_fs import DaytonaSessionWorkspaceFS
        from fleet_rlm.daytona.workspace_memory import DaytonaWorkspaceMemoryStore
        from fleet_rlm.files.memory_tools import WorkspaceMemoryToolHost
        from fleet_rlm.files.project_tools import ProjectToolHost
        from fleet_rlm.files.tools import FileToolHost
        from fleet_rlm.files.url_tool import UrlToolHost, WorkspaceUrlSourceStore
        from fleet_rlm.files.workspace_tools import WorkspaceToolHost

        sink = environment.attachment_sink
        volume_fs = cast(_DaytonaRunSink, sink).volume_fs
        assert volume_fs is not None  # _DaytonaRunSink is always constructed with loop
        paths = volume_paths_from_settings(self.settings)
        file_host = FileToolHost(
            attachments=attachments.refs,
            staged_attachments=attachments.staged,
            volume_fs=volume_fs,
            user_id=run.access.user_id,
            workspace_id=run.access.workspace_id,
            session_id=run.session_id,
            run_id=run.run_id,
            max_artifact_bytes=self.settings.max_artifact_bytes,
            volume_paths=paths,
        )
        session_workspace = DaytonaSessionWorkspaceFS(
            volume_fs.sandbox,
            volume_root=str(paths.mount_path),
            root=str(paths.session_workspace_dir(run.session_id)),
            max_file_bytes=self.settings.max_upload_bytes,
        )
        workspace_host = WorkspaceToolHost(
            session_workspace,
            max_file_bytes=self.settings.max_upload_bytes,
        )
        projects_fs = DaytonaSessionWorkspaceFS(
            volume_fs.sandbox,
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
                DaytonaSessionWorkspaceFS(
                    volume_fs.sandbox,
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
            memory_store = DaytonaWorkspaceMemoryStore(
                volume_fs.sandbox,
                volume_paths=paths,
                max_upload_bytes=self.settings.max_upload_bytes,
            )
        memory_host = WorkspaceMemoryToolHost(memory_store)
        memory_candidates = None
        candidate_tools: tuple[Any, ...] = ()
        candidate_views: dict[str, Any] = {}
        if self.settings.rlm_autonomous_memory_categories:
            from fleet_rlm.files.memory_candidate_tools import MemoryCandidateToolHost
            from fleet_rlm.files.memory_candidates import MemoryCandidateCollector

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
        file_tools = file_host.as_tools()
        workspace_tools = workspace_host.as_tools()
        project_tools = project_host.as_tools()
        memory_tools = memory_host.as_tools()
        url_tools = url_host.as_tools()
        base_views = {
            **file_host.event_views(),
            **workspace_host.event_views(),
            **project_host.event_views(),
            **memory_host.event_views(),
            **candidate_views,
            **url_host.event_views(),
        }
        spec, skill_host, notices = await prepare_host_capabilities(
            turn=run,
            skill_catalog=self.skill_catalog,
            base_tools=(*file_tools, *workspace_tools, *project_tools, *memory_tools, *candidate_tools, *url_tools),
            base_event_views=base_views,
            workspace=DAYTONA_WORKSPACE_CAPABILITY,
            workspace_fs=session_workspace,
            deadline=deadline,
        )
        return LivePreparedCapabilities(
            spec,
            files=file_host,
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
        self._sandbox_ids: list[str] = []

    def track_sandbox(self, sandbox_id: str | None) -> None:
        if sandbox_id and sandbox_id not in self._sandbox_ids:
            self._sandbox_ids.append(sandbox_id)

    def forget_sandboxes(self) -> None:
        """Drop tracked sandbox ids without deleting (API-restart simulation)."""
        self._sandbox_ids.clear()

    async def cleanup(self) -> None:
        """Delete tracked sandboxes (best-effort)."""
        for sid in list(self._sandbox_ids):
            with contextlib.suppress(Exception):
                await self.platform.delete(sid)
        self._sandbox_ids.clear()

    async def adispose(self) -> None:
        """Delete tracked sandboxes and close Daytona clients."""
        await self.session_manager.aclose()
        await self.cleanup()
        await self.client.close()


def build_run_preparation(
    resources: DaytonaRuntimeResources,
    *,
    attachment_lifecycle: Any,
    skill_catalog: SkillCatalog,
    settings: Settings,
    models: RLMModelBundle,
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
        attachments=_LiveAttachmentLifecycle(attachment_lifecycle),
        environments=_DaytonaEnvironmentProvider(resources, settings),
        capabilities=_LiveCapabilityPreparer(settings, skill_catalog),
    )
