"""Daytona Run environment inventory and exact Turn preparation adapter."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fleet_rlm.chat.capability_preparation import (
    PreparedHostCapabilities,
    prepare_host_capabilities,
)
from fleet_rlm.chat.turn_lifecycle import ExecuteTurn
from fleet_rlm.chat.turn_preparation import (
    DefaultTurnPreparer,
    RunEnvironment,
    TurnPreparationTimeoutError,
    TurnPreparationUnavailableError,
)
from fleet_rlm.composition.common import recursive_rlm_options
from fleet_rlm.config import Settings, load_runtime_settings
from fleet_rlm.daytona.bindings import BindingStore, InMemoryBindingStore
from fleet_rlm.daytona.interpreter import sync_sandbox
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
    DaytonaAdmission,
    DaytonaAdmissionTimeoutError,
    DaytonaLeaseAcquisitionTimeoutError,
    DaytonaSessionManager,
    LeaseRequest,
)
from fleet_rlm.daytona.workspace_fs import AsyncDaytonaVolumeFS, DaytonaSandboxVolumeFs
from fleet_rlm.files.models import (
    AttachmentAccess,
    AttachmentRun,
    PreparedAttachments,
)
from fleet_rlm.files.volume_paths import VolumePaths, volume_paths_from_settings
from fleet_rlm.files.workspace_models import DAYTONA_WORKSPACE_CAPABILITY
from fleet_rlm.persistence.database import (
    create_async_engine_from_url,
    create_session_factory,
    create_tables,
)
from fleet_rlm.rlm.context import RLMExecutionSpec
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.lm_factory import build_model_bundle
from fleet_rlm.skills.catalog import SkillCatalog


async def _settle_owned_thread(task: asyncio.Task[Any]) -> bool:
    """Wait through repeated caller cancellation until owned thread work exits."""
    cancellation_requested = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancellation_requested = True
        except BaseException:
            break
    return cancellation_requested


def _consume_task_result(task: asyncio.Task[Any]) -> None:
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
    ) -> None:
        super().__init__(
            spec,
            files=files,
            skills=skills,
            close_files=True,
            artifact_candidates=True,
            preparation_notices=preparation_notices,
        )


class _DaytonaRunSink:
    def __init__(
        self,
        sandbox: Any,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        max_read_bytes: int,
        paths: VolumePaths,
    ) -> None:
        self._sandbox = sandbox
        self._files = AsyncDaytonaVolumeFS(sandbox)
        self.volume_fs = DaytonaSandboxVolumeFs(sync_sandbox(sandbox, loop)) if loop is not None else None
        self._max_read_bytes = max_read_bytes
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

    async def read_private(self, logical_path: str) -> bytes:
        return await self.read(logical_path, max_bytes=self._max_read_bytes)

    async def write_private(self, logical_path: str, data: bytes) -> None:
        await self.write(logical_path, data)

    async def remove_private(self, logical_path: str) -> None:
        await self.remove(logical_path)


@dataclass(slots=True)
class _DaytonaEnvironmentProvider:
    resources: LiveKernelResources

    async def acquire(self, turn: ExecuteTurn, *, deadline: float) -> RunEnvironment:
        try:
            lease = await self.resources.session_manager.acquire(
                LeaseRequest(
                    session_id=turn.session_id,
                    user_id=turn.access.user_id,
                    workspace_id=turn.access.workspace_id,
                    run_id=turn.run_id,
                ),
                deadline=deadline,
            )
        except DaytonaAdmissionTimeoutError as exc:
            raise TurnPreparationUnavailableError("Turn environment is unavailable") from exc
        except DaytonaLeaseAcquisitionTimeoutError as exc:
            raise TurnPreparationTimeoutError("Turn preparation timed out") from exc
        try:
            self.resources.track_sandbox(lease.sandbox_id)
            lookup = asyncio.create_task(self.resources.platform.get(lease.sandbox_id))
            try:
                async with asyncio.timeout_at(deadline):
                    sandbox = await asyncio.shield(lookup)
            except TimeoutError:
                if lookup.done():
                    raise
                cancelled = await _settle_owned_thread(lookup)
                _consume_task_result(lookup)
                if cancelled:
                    raise asyncio.CancelledError from None
                raise TurnPreparationTimeoutError("Turn preparation timed out") from None
            except asyncio.CancelledError:
                await _settle_owned_thread(lookup)
                _consume_task_result(lookup)
                raise
            if sandbox is None:
                raise RuntimeError("acquired Sandbox is unavailable")
            sink = _DaytonaRunSink(
                sandbox,
                loop=asyncio.get_running_loop(),
                max_read_bytes=self.resources.settings.max_upload_bytes,
                paths=volume_paths_from_settings(self.resources.settings),
            )

            async def release() -> None:
                await self.resources.session_manager.release(lease)

            main_loop = asyncio.get_running_loop()
            child_runtime_factory = build_child_runtime_factory(
                loop=main_loop,
                platform=self.resources.platform,
                admission=self.resources.daytona_admission,
                volume_id=lease.volume_id,
                mount_path=self.resources.volume_config.mount_path,
                workspace_id=turn.access.workspace_id,
                run_id=turn.run_id,
                deadline=deadline,
                execution_timeout_s=self.resources.settings.rlm_execution_timeout_s,
                execution_output_cap=self.resources.settings.rlm_max_execution_output_chars,
                is_authorized=lambda: not turn.authority.revoked,
            )

            return RunEnvironment(
                interpreter=lease.interpreter,
                attachment_sink=sink,
                artifact_sink=sink,
                release=release,
                result_snapshot_sink=sink,
                child_runtime_factory=child_runtime_factory,
                context_mount_path=str(volume_paths_from_settings(self.resources.settings).mount_path),
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


@dataclass(slots=True)
class _LiveCapabilityPreparer:
    resources: LiveKernelResources
    skill_catalog: SkillCatalog

    async def prepare(
        self,
        turn: ExecuteTurn,
        environment: RunEnvironment,
        attachments: PreparedAttachments,
        *,
        deadline: float,
    ) -> LivePreparedCapabilities:
        """
        Prepare the file, workspace, URL, and memory capabilities for a turn.

        Parameters:
            deadline (float): Deadline for capability preparation.

        Returns:
            LivePreparedCapabilities: Prepared capabilities and any preparation notices.
        """
        from fleet_rlm.daytona.workspace_fs import DaytonaSessionWorkspaceFS
        from fleet_rlm.daytona.workspace_memory import DaytonaWorkspaceMemoryStore
        from fleet_rlm.files.memory_tools import WorkspaceMemoryToolHost
        from fleet_rlm.files.tools import FileToolHost
        from fleet_rlm.files.url_tool import UrlToolHost, WorkspaceUrlSourceStore
        from fleet_rlm.files.workspace_tools import WorkspaceToolHost

        sink = environment.attachment_sink
        volume_fs = cast(_DaytonaRunSink, sink).volume_fs
        assert volume_fs is not None  # _DaytonaRunSink is always constructed with loop
        paths = volume_paths_from_settings(self.resources.settings)
        file_host = FileToolHost(
            attachments=attachments.refs,
            staged_attachments=attachments.staged,
            volume_fs=volume_fs,
            user_id=turn.access.user_id,
            workspace_id=turn.access.workspace_id,
            session_id=turn.session_id,
            run_id=turn.run_id,
            max_artifact_bytes=self.resources.settings.max_artifact_bytes,
            volume_paths=paths,
        )
        session_workspace = DaytonaSessionWorkspaceFS(
            volume_fs.sandbox,
            volume_root=str(paths.mount_path),
            root=str(paths.session_workspace_dir(turn.session_id)),
            max_file_bytes=self.resources.settings.max_upload_bytes,
        )
        workspace_host = WorkspaceToolHost(
            session_workspace,
            max_file_bytes=self.resources.settings.max_upload_bytes,
        )
        url_host = UrlToolHost(
            session_id=turn.session_id,
            store=WorkspaceUrlSourceStore(
                DaytonaSessionWorkspaceFS(
                    volume_fs.sandbox,
                    volume_root=str(paths.mount_path),
                    root=str(paths.session_workspace_dir(turn.session_id)),
                    max_file_bytes=self.resources.settings.max_url_bytes,
                )
            ),
            max_bytes=self.resources.settings.max_url_bytes,
        )
        memory_host = WorkspaceMemoryToolHost(
            DaytonaWorkspaceMemoryStore(
                volume_fs.sandbox,
                volume_paths=paths,
                max_upload_bytes=self.resources.settings.max_upload_bytes,
            )
        )
        file_tools = file_host.as_tools()
        workspace_tools = workspace_host.as_tools()
        memory_tools = memory_host.as_tools()
        url_tools = url_host.as_tools()
        base_views = {
            **file_host.event_views(),
            **workspace_host.event_views(),
            **memory_host.event_views(),
            **url_host.event_views(),
        }
        spec, skill_host, notices = await prepare_host_capabilities(
            turn=turn,
            skill_catalog=self.skill_catalog,
            files=file_host,
            base_tools=(*file_tools, *workspace_tools, *memory_tools, *url_tools),
            base_event_views=base_views,
            workspace=DAYTONA_WORKSPACE_CAPABILITY,
            deadline=deadline,
        )
        return LivePreparedCapabilities(
            spec,
            files=file_host,
            skills=skill_host,
            preparation_notices=notices,
        )


def resolve_settings(settings: Settings | None = None) -> Settings:
    """Return explicit settings or load the resolved TOML policy."""
    return settings or load_runtime_settings()


class LiveKernelResources:
    """Holds live clients for one process; delete sandboxes on cleanup.

    Default: in-memory bindings (kernel smoke).
    Pass ``session_factory`` for durable bindings and coordinated Turn state.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        bindings: Any | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        engine: AsyncEngine | None = None,
        sandbox_spec: DaytonaSandboxSpec | None = None,
        cleanup: Any | None = None,
    ) -> None:
        self.settings = resolve_settings(settings)
        self.sandbox_spec = sandbox_spec or sandbox_spec_from_settings(self.settings)
        self.client = build_daytona_client(self.settings)
        self.platform = LiveDaytonaPlatform(self.client, self.sandbox_spec)
        self.volume_client = LiveDaytonaVolumeClient(self.client)
        self.volume_config = volume_config_from_settings(self.settings)
        self._engine = engine
        self._session_factory = session_factory
        if bindings is not None:
            self.bindings = bindings
        elif session_factory is not None:
            self.bindings = BindingStore(session_factory)
        else:
            self.bindings = InMemoryBindingStore()
        self.daytona_admission = DaytonaAdmission(
            max_active_leases=self.settings.max_active_daytona_leases,
        )
        self.session_manager = DaytonaSessionManager(
            platform=self.platform,
            volume_client=self.volume_client,
            volume_config=self.volume_config,
            bindings=self.bindings,
            admission=self.daytona_admission,
            sandbox_spec=self.sandbox_spec,
            cleanup=cleanup,
            idle_stop_seconds=DEFAULT_IDLE_STOP_SECONDS,
            execution_output_cap=self.settings.rlm_max_execution_output_chars,
            execution_timeout_s=self.settings.rlm_execution_timeout_s,
        )
        self.models = build_model_bundle(self.settings)
        self._sandbox_ids: list[str] = []

    @classmethod
    async def with_sqlite_file(
        cls,
        db_path: Path | str,
        settings: Settings | None = None,
    ) -> LiveKernelResources:
        """Durable sqlite-backed bindings + sessions for recovery proofs."""
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{path.resolve()}"
        engine = create_async_engine_from_url(url)
        await create_tables(engine)
        factory = create_session_factory(engine)
        return cls(
            settings or Settings(),
            session_factory=factory,
            engine=engine,
        )

    @classmethod
    async def reopen_sqlite_file(
        cls,
        db_path: Path | str,
        settings: Settings | None = None,
    ) -> LiveKernelResources:
        """Simulate API restart: new clients against the same sqlite file."""
        return await cls.with_sqlite_file(
            db_path,
            settings,
        )

    def track_sandbox(self, sandbox_id: str | None) -> None:
        if sandbox_id and sandbox_id not in self._sandbox_ids:
            self._sandbox_ids.append(sandbox_id)

    def forget_sandboxes(self) -> None:
        """Drop tracked sandbox ids without deleting (API-restart simulation)."""
        self._sandbox_ids.clear()

    @property
    def engine(self) -> AsyncEngine | None:
        return self._engine

    async def cleanup(self) -> None:
        """Delete tracked sandboxes (best-effort). Does not dispose the DB engine."""
        for sid in list(self._sandbox_ids):
            with contextlib.suppress(Exception):
                await self.platform.delete(sid)
        self._sandbox_ids.clear()

    async def adispose_engine(self) -> None:
        """Dispose the sqlite engine without deleting sandboxes."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    async def adispose(self) -> None:
        """Delete sandboxes and dispose engine (end of proof)."""
        await self.session_manager.aclose()
        await self.cleanup()
        await self.adispose_engine()
        await self.client.close()


def build_turn_preparation(
    resources: LiveKernelResources,
    *,
    attachment_lifecycle: Any,
    skill_catalog: SkillCatalog,
) -> DefaultTurnPreparer:
    """Compose Daytona Turn preparation without mutating resource ownership."""
    options = RLMOptions(
        max_iterations=resources.settings.rlm_max_iterations,
        max_llm_calls=resources.settings.rlm_max_llm_calls,
        max_output_chars=resources.settings.rlm_max_output_chars,
    )
    return DefaultTurnPreparer(
        models=resources.models,
        options=options,
        recursive_options=recursive_rlm_options(resources.settings),
        attachments=_LiveAttachmentLifecycle(attachment_lifecycle),
        environments=_DaytonaEnvironmentProvider(resources),
        capabilities=_LiveCapabilityPreparer(resources, skill_catalog),
    )
