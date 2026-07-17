"""Daytona Run environment inventory and exact Turn preparation adapter."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fleet_rlm.chat.turn_lifecycle import ExecuteTurn
from fleet_rlm.chat.turn_preparation import (
    PreparedTurn,
    RunEnvironment,
    TurnPreparationCancelled,
    TurnPreparationModule,
    TurnPreparationTimeout,
    TurnPreparationUnavailable,
)
from fleet_rlm.config import Settings
from fleet_rlm.daytona.admission import DaytonaAdmission, DaytonaAdmissionTimeout
from fleet_rlm.daytona.bindings import BindingStore, InMemoryBindingStore
from fleet_rlm.daytona.client import build_daytona_client
from fleet_rlm.daytona.paths import VolumePaths, volume_paths_from_settings
from fleet_rlm.daytona.platform import LiveDaytonaPlatform, LiveDaytonaVolumeClient
from fleet_rlm.daytona.session_manager import DaytonaLeaseAcquisitionTimeout, DaytonaSessionManager, LeaseRequest
from fleet_rlm.daytona.volumes import volume_config_from_settings
from fleet_rlm.files.models import (
    AttachmentAccess,
    AttachmentRun,
    PreparedAttachments,
    StagedAttachment,
)
from fleet_rlm.files.workspace_models import DAYTONA_WORKSPACE_CAPABILITY
from fleet_rlm.persistence.database import (
    create_async_engine_from_url,
    create_session_factory,
    create_tables,
)
from fleet_rlm.rlm.context import PreparationNotice
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.events import AttachmentRead, SkillActivated, SkillLoaded
from fleet_rlm.rlm.lm_factory import build_model_bundle
from fleet_rlm.sessions.history_tools import SessionHistoryToolHost
from fleet_rlm.skills.capabilities import (
    CapabilityRegistry,
    CapabilityResolutionContext,
    CapabilityResolver,
    TurnCapabilityBlueprint,
)


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
    try:
        task.result()
    except BaseException:
        pass


class LivePreparedCapabilities:
    """Run-bound Skill/Attachment tools and their typed public ledgers."""

    def __init__(
        self,
        blueprint: TurnCapabilityBlueprint,
        *,
        files: Any,
        skills: Any,
        preparation_notices: tuple[PreparationNotice, ...] = (),
    ) -> None:
        self.blueprint = blueprint
        self._files = files
        self._skills = skills
        self.preparation_notices = preparation_notices

    def drain_public_details(self) -> tuple[AttachmentRead | SkillActivated | SkillLoaded, ...]:
        values: list[AttachmentRead | SkillActivated | SkillLoaded] = []
        for item in self._files.drain_public_events():
            values.append(
                AttachmentRead(
                    UUID(item["attachment_id"]),
                    str(item["filename"]),
                    int(item["byte_size"]),
                )
            )
        for item in self._skills.drain_public_events():
            values.append(_skill_event(item))
        return tuple(values)

    def drain_artifact_candidates(self):
        return self._files.drain_artifact_candidates()

    async def aclose(self) -> None:
        return None


class _EmptySkillHost:
    def drain_public_events(self) -> list[dict[str, Any]]:
        return []


def _skill_event(item: dict[str, Any]) -> SkillActivated | SkillLoaded:
    if item.get("kind") == "skill.activated":
        return SkillActivated(
            str(item["skill_id"]),
            str(item["name"]),
            str(item["version"]),
            str(item["trust"]),
            tuple(str(value) for value in item.get("affordances", ())),
        )
    return SkillLoaded(str(item["skill_id"]), str(item["name"]), str(item["version"]))


class _DaytonaRunSink:
    def __init__(self, volume_fs: Any, *, max_read_bytes: int, paths: VolumePaths) -> None:
        self.volume_fs = volume_fs
        self._max_read_bytes = max_read_bytes
        self._paths = paths

    def result_path(self, session_id: UUID, run_id: UUID) -> str:
        return str(self._paths.run_result_path(session_id, run_id))

    async def read(self, location: str, *, max_bytes: int) -> bytes:
        import asyncio

        value = await asyncio.to_thread(self.volume_fs.read_bytes, location)
        if len(value) > max_bytes:
            raise ValueError("value exceeds read bound")
        return value

    async def write(self, location: str, data: bytes) -> None:
        import asyncio

        await asyncio.to_thread(self.volume_fs.write_bytes, location, data)

    async def remove(self, location: str) -> None:
        import asyncio

        await asyncio.to_thread(self.volume_fs.remove, location)

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
        except DaytonaAdmissionTimeout as exc:
            raise TurnPreparationUnavailable("Turn environment is unavailable") from exc
        except DaytonaLeaseAcquisitionTimeout as exc:
            raise TurnPreparationTimeout("Turn preparation timed out") from exc
        try:
            self.resources.track_sandbox(lease.sandbox_id)
            from fleet_rlm.daytona.volume_fs import DaytonaSandboxVolumeFs

            lookup = asyncio.create_task(asyncio.to_thread(self.resources.platform.get, lease.sandbox_id))
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
                raise TurnPreparationTimeout("Turn preparation timed out") from None
            except asyncio.CancelledError:
                await _settle_owned_thread(lookup)
                _consume_task_result(lookup)
                raise
            if sandbox is None:
                raise RuntimeError("acquired Sandbox is unavailable")
            sink = _DaytonaRunSink(
                DaytonaSandboxVolumeFs(sandbox),
                max_read_bytes=self.resources.settings.max_upload_bytes,
                paths=volume_paths_from_settings(self.resources.settings),
            )

            async def release() -> None:
                await self.resources.session_manager.release(lease)

            return RunEnvironment(lease.interpreter, sink, sink, release, sink)
        except BaseException:
            await asyncio.shield(self.resources.session_manager.release(lease))
            raise


@dataclass(slots=True)
class _LiveAttachmentLifecycle:
    resources: LiveKernelResources

    async def prepare_run(
        self,
        access: AttachmentAccess,
        attachment_ids: Sequence[UUID],
        run: AttachmentRun,
        sink: Any,
    ) -> PreparedAttachments:
        attachment_lifecycle = getattr(self.resources, "attachment_lifecycle", None)
        if attachment_lifecycle is not None:
            return await attachment_lifecycle.prepare_run(
                access,
                attachment_ids,
                run,
                sink,
            )
        store = self.resources.attachment_store
        if store is None:
            if attachment_ids:
                raise RuntimeError("live Attachment storage is unavailable")
            return PreparedAttachments((), ())
        paths = volume_paths_from_settings(self.resources.settings)
        refs = []
        staged = []
        for attachment_id in attachment_ids:
            ref = await store.get(
                attachment_id,
                user_id=access.user_id,
                workspace_id=access.workspace_id,
            )
            data = await store.read_bytes(
                attachment_id,
                user_id=access.user_id,
                workspace_id=access.workspace_id,
            )
            if len(data) != ref.byte_size or hashlib.sha256(data).hexdigest() != ref.checksum_sha256:
                raise RuntimeError("Attachment failed integrity validation")
            logical_path = str(paths.run_attachment_file(run.session_id, run.run_id, ref.id, ref.filename))
            await sink.write_private(logical_path, data)
            refs.append(ref)
            staged.append(StagedAttachment(ref.id, logical_path))
        return PreparedAttachments(tuple(refs), tuple(staged))


@dataclass(slots=True)
class _LiveCapabilityPreparer:
    resources: LiveKernelResources

    async def prepare(
        self,
        turn: ExecuteTurn,
        environment: RunEnvironment,
        attachments: PreparedAttachments,
        *,
        deadline: float,
    ) -> LivePreparedCapabilities:
        from fleet_rlm.daytona.workspace_fs import DaytonaSessionWorkspaceFS
        from fleet_rlm.files.tools import FileToolHost
        from fleet_rlm.files.workspace_tools import WorkspaceToolHost
        from fleet_rlm.skills.authorize import SkillAuthorizer
        from fleet_rlm.skills.tools import SkillToolHost

        sink = environment.attachment_sink
        volume_fs = getattr(sink, "volume_fs")
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
        workspace_host = WorkspaceToolHost(
            DaytonaSessionWorkspaceFS(
                volume_fs.sandbox,
                volume_root=str(paths.mount_path),
                root=str(paths.session_workspace_dir(turn.session_id)),
                max_file_bytes=self.resources.settings.max_upload_bytes,
            ),
            max_file_bytes=self.resources.settings.max_upload_bytes,
        )
        file_tools = file_host.as_tools()
        workspace_tools = workspace_host.as_tools()
        history_host = SessionHistoryToolHost(turn.history)
        history_tools = history_host.as_tools()
        base_views = {
            **file_host.event_views(),
            **workspace_host.event_views(),
            **history_host.event_views(),
        }
        if self.resources.skill_registry is None:
            if turn.input.skill_selections:
                from fleet_rlm.skills.authorize import InvalidSkillSelectionError

                raise InvalidSkillSelectionError()
            return LivePreparedCapabilities(
                TurnCapabilityBlueprint(
                    tools=(*file_tools, *workspace_tools, *history_tools),
                    tool_event_views=base_views,
                    workspace=DAYTONA_WORKSPACE_CAPABILITY,
                ),
                files=file_host,
                skills=_EmptySkillHost(),
            )
        authorizer = SkillAuthorizer(self.resources.skill_registry)
        selections = tuple(turn.input.skill_selections)
        try:
            if getattr(self.resources.skill_registry, "unavailable", False):
                raise RuntimeError("skills unavailable")
            cards = authorizer.list_cards(user_id=turn.access.user_id, workspace_id=turn.access.workspace_id)
            selected_records = (
                authorizer.authorize_explicit_many(
                    selections,
                    user_id=turn.access.user_id,
                    workspace_id=turn.access.workspace_id,
                )
                if selections
                else ()
            )
        except Exception as exc:
            from fleet_rlm.skills.authorize import InvalidSkillSelectionError

            if selections:
                if isinstance(exc, InvalidSkillSelectionError):
                    raise
                raise InvalidSkillSelectionError() from None
            return LivePreparedCapabilities(
                TurnCapabilityBlueprint(
                    tools=(*file_tools, *workspace_tools, *history_tools),
                    tool_event_views=base_views,
                    workspace=DAYTONA_WORKSPACE_CAPABILITY,
                ),
                files=file_host,
                skills=_EmptySkillHost(),
                preparation_notices=(PreparationNotice("skills_unavailable", "Skills are unavailable"),),
            )
        if await turn.cancellation_requested():
            raise TurnPreparationCancelled("Turn cancelled")
        if asyncio.get_running_loop().time() >= deadline:
            raise TurnPreparationTimeout("Turn preparation timed out")
        skill_host = SkillToolHost(
            authorizer,
            user_id=turn.access.user_id,
            workspace_id=turn.access.workspace_id,
            allowed_skill_ids=(frozenset(record.id for record in selected_records) if selections else None),
        )
        tools = (
            *file_tools,
            *workspace_tools,
            *history_tools,
            *skill_host.as_tools(),
        )
        tool_event_views = {**base_views, **skill_host.event_views()}
        try:
            blueprint = await CapabilityResolver(self.resources.capability_registry or CapabilityRegistry()).resolve(
                CapabilityResolutionContext(
                    request=turn.input.text,
                    history=[{"role": item.role, "content": item.content} for item in turn.history.messages],
                    models=self.resources.models,
                    options=RLMOptions(
                        max_iterations=self.resources.settings.rlm_max_iterations,
                        max_llm_calls=self.resources.settings.rlm_max_llm_calls,
                        max_output_chars=self.resources.settings.rlm_max_output_chars,
                    ),
                    skill_cards=cards,
                    selected_skills=selected_records,
                    attachments=attachments.refs,
                    tools=tools,
                    tool_event_views=tool_event_views,
                    workspace=DAYTONA_WORKSPACE_CAPABILITY,
                    deadline=deadline,
                )
            )
        except TimeoutError as exc:
            raise TurnPreparationTimeout("Turn preparation timed out") from exc
        except Exception:
            from fleet_rlm.skills.authorize import InvalidSkillSelectionError

            if selections:
                raise InvalidSkillSelectionError() from None
            raise
        for record in selected_records:
            skill_host.mark_preloaded(record)
        return LivePreparedCapabilities(blueprint, files=file_host, skills=skill_host)


def resolve_settings(settings: Settings | None = None) -> Settings:
    """Return explicit settings or resolve the canonical ``FLEET_*`` surface."""
    return settings or Settings()


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
    ) -> None:
        self.settings = resolve_settings(settings)
        self.client = build_daytona_client(self.settings)
        self.platform = LiveDaytonaPlatform(self.client)
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
        )
        self.models = build_model_bundle(self.settings)
        self._sandbox_ids: list[str] = []
        # Optional capability hosts (wired by live proofs / app composition)
        self.skill_registry: Any | None = None
        self.capability_registry: Any | None = None
        self.attachment_store: Any | None = None
        self.artifact_store: Any | None = None
        self.attachment_lifecycle: Any | None = None
        self._preparation: TurnPreparationModule | None = None

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

    async def prepare(self, turn: ExecuteTurn) -> PreparedTurn:
        """Acquire exactly one live Interpreter Lease before stream construction."""
        preparation = getattr(self, "_preparation", None)
        if preparation is None:
            options = RLMOptions(
                max_iterations=self.settings.rlm_max_iterations,
                max_llm_calls=self.settings.rlm_max_llm_calls,
                max_output_chars=self.settings.rlm_max_output_chars,
            )
            preparation = TurnPreparationModule(
                models=self.models,
                options=options,
                turn_timeout_seconds=self.settings.turn_timeout_seconds,
                attachments=_LiveAttachmentLifecycle(self),
                environments=_DaytonaEnvironmentProvider(self),
                capabilities=_LiveCapabilityPreparer(self),
            )
        return await preparation.prepare(turn)

    def configure_preparation(self, attachment_lifecycle: Any) -> None:
        options = RLMOptions(
            max_iterations=self.settings.rlm_max_iterations,
            max_llm_calls=self.settings.rlm_max_llm_calls,
            max_output_chars=self.settings.rlm_max_output_chars,
        )
        self.attachment_lifecycle = attachment_lifecycle
        self._preparation = TurnPreparationModule(
            models=self.models,
            options=options,
            turn_timeout_seconds=self.settings.turn_timeout_seconds,
            attachments=_LiveAttachmentLifecycle(self),
            environments=_DaytonaEnvironmentProvider(self),
            capabilities=_LiveCapabilityPreparer(self),
        )

    def track_sandbox(self, sandbox_id: str | None) -> None:
        if sandbox_id and sandbox_id not in self._sandbox_ids:
            self._sandbox_ids.append(sandbox_id)

    def forget_sandboxes(self) -> None:
        """Drop tracked sandbox ids without deleting (API-restart simulation)."""
        self._sandbox_ids.clear()

    def cleanup(self) -> None:
        """Delete tracked sandboxes (best-effort). Does not dispose the DB engine."""
        for sid in list(self._sandbox_ids):
            try:
                self.platform.delete(sid)
            except Exception:  # noqa: BLE001 - best-effort live cleanup
                pass
        self._sandbox_ids.clear()

    async def adispose_engine(self) -> None:
        """Dispose the sqlite engine without deleting sandboxes."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    async def adispose(self) -> None:
        """Delete sandboxes and dispose engine (end of proof)."""
        self.cleanup()
        await self.adispose_engine()
