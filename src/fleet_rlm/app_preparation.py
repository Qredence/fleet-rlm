"""Concrete Daytona preparation and release for one claimed Turn."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any

from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.diagnostics import environment_manifest
from fleet_rlm.daytona.interpreter import (
    SyncBridgeDispatcher,
    sandbox_interpreter_factory,
)
from fleet_rlm.daytona.leases import DaytonaAdmissionTimeoutError, DaytonaLeaseAcquisitionTimeoutError, InterpreterLease
from fleet_rlm.daytona.provider import remove_run_scratch
from fleet_rlm.daytona.runtime import DaytonaRuntime, RootSessionSpec
from fleet_rlm.daytona.specs import DaytonaSandboxSpec, ensure_volume_layout
from fleet_rlm.paths import SESSION_WORKSPACE_MOUNT_PATH, VolumePaths
from fleet_rlm.rlm.budget import BudgetLimits
from fleet_rlm.rlm.ownership import OwnedEffect
from fleet_rlm.rlm.program import RLMModelBundle, rlm_options
from fleet_rlm.rlm.recursion import recursive_rlm_options
from fleet_rlm.sessions.run_state import ClaimedRun
from fleet_rlm.sessions.task import SessionTaskService
from fleet_rlm.skills.catalog import SkillCatalog
from fleet_rlm.turns.preparation import (
    DaytonaCapabilityPreparer,
    RunEnvironment,
    RunPreparationTimeoutError,
    RunPreparationUnavailableError,
    TurnPreparationPlan,
)
from fleet_rlm.workspace.artifacts import ArtifactReader
from fleet_rlm.workspace.attachments import AttachmentLifecycle
from fleet_rlm.workspace.host_io import DaytonaHostIO, DaytonaRunStorage
from fleet_rlm.workspace.mounted_gateway import (
    DaytonaWorkspaceGateway,
    DaytonaWorkspaceVolumeGateway,
)
from fleet_rlm.workspace.storage import DaytonaSandboxWorkspaceStorage

logger = logging.getLogger(__name__)


async def _cleanup_scratch_before_releasing_invocation(
    cleanup: Callable[[], Any] | None,
    release_invocation: Callable[[], None] | None,
) -> None:
    """Keep the Session invocation gate held until threaded scratch cleanup settles."""
    try:
        if cleanup is None:
            return
        effect = OwnedEffect.start(asyncio.to_thread(cleanup))
        settled = await effect.settle()
        if settled.caller_cancelled:
            raise asyncio.CancelledError
    finally:
        if release_invocation is not None:
            release_invocation()


@dataclass(frozen=True, slots=True, kw_only=True)
class DaytonaRunPreparation:
    """Prepare Turn-scoped bindings while the runtime owns retained Sandboxes."""

    runtime: DaytonaRuntime
    attachment_lifecycle: AttachmentLifecycle
    skill_catalog: SkillCatalog
    settings: Settings
    models: RLMModelBundle
    volume_paths: VolumePaths
    sandbox_spec: DaytonaSandboxSpec
    dispatcher: SyncBridgeDispatcher
    workspace_gateway: DaytonaWorkspaceGateway
    volume_gateway: DaytonaWorkspaceVolumeGateway
    artifact_reader: ArtifactReader | None = None
    task_service: SessionTaskService | None = None

    @staticmethod
    def context_key(run: ClaimedRun) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...], str | None]:
        attachment_ids = tuple(str(attachment_id) for attachment_id in run.input.attachment_ids)
        return (
            attachment_ids,
            tuple((str(selection.id), str(selection.expected_version)) for selection in run.input.skill_selections),
            str(run.run_id) if attachment_ids else None,
        )

    async def acquire_environment(self, run: ClaimedRun, *, deadline: float) -> RunEnvironment:
        key = (run.access.workspace_id, run.session_id)
        release_invocation: Callable[[], None] | None = None
        owner: InterpreterLease | None = None
        try:
            try:
                release_invocation = await self.runtime.begin_root_invocation(*key, deadline=deadline)
            except TimeoutError:
                raise RunPreparationTimeoutError("Turn preparation timed out") from None

            async with self.workspace_gateway.open_sandbox(
                run.access.workspace_id, purpose="session-volume-layout"
            ) as io_sandbox:
                await ensure_volume_layout(
                    io_sandbox,
                    self.volume_paths,
                    session_id=run.session_id,
                    run_id=run.run_id,
                )

            try:
                owner = await self.runtime.acquire_root_session(
                    RootSessionSpec(
                        workspace_id=key[0],
                        session_id=key[1],
                        user_id=run.access.user_id,
                        run_id=run.run_id,
                        context_fingerprint=self.context_key(run),
                        deadline=deadline,
                    )
                )
            except DaytonaAdmissionTimeoutError as exc:
                raise RunPreparationUnavailableError("Turn environment is unavailable") from exc
            except (DaytonaLeaseAcquisitionTimeoutError, TimeoutError) as exc:
                raise RunPreparationTimeoutError("Turn preparation timed out") from exc

            if owner is None:
                raise RuntimeError("acquired Sandbox owner is unavailable")
            self.runtime.track_sandbox(owner.sandbox_id)
            sandbox = owner.sandbox
            if sandbox is None:
                raise RuntimeError("acquired Sandbox is unavailable")

            await self.runtime.suspend_root_autostop(owner, deadline=deadline)

            host_io = DaytonaHostIO(
                run.access.workspace_id,
                volume_gateway=self.volume_gateway,
                workspace_gateway=self.workspace_gateway,
                dispatcher=self.dispatcher,
                volume_root=str(self.volume_paths.mount_path),
                max_file_bytes=self.settings.max_upload_bytes,
            )
            sink = DaytonaRunStorage(
                sandbox,
                dispatcher=self.dispatcher,
                paths=self.volume_paths,
                host_io=host_io,
                run_id=run.run_id,
            )
            session_workspace = DaytonaSandboxWorkspaceStorage(
                sink.sandbox,
                volume_root=SESSION_WORKSPACE_MOUNT_PATH,
                root=SESSION_WORKSPACE_MOUNT_PATH,
                max_file_bytes=self.settings.max_upload_bytes,
                allow_volume_root=True,
            )
            project_workspace = host_io.workspace_storage(str(self.volume_paths.projects_root()))

            async def release_preparation() -> None:
                try:

                    def cleanup_run_scratch() -> None:
                        remove_run_scratch(sink.sandbox, sink.scratch_root)

                    await _cleanup_scratch_before_releasing_invocation(
                        cleanup_run_scratch if not self.runtime.root_is_tainted(*key) else None,
                        None,
                    )
                    await self.runtime.restore_root_autostop(owner)
                except BaseException:
                    self.runtime.mark_root_tainted(*key)
                    await self.runtime.restore_root_autostop(owner)
                    raise
                finally:
                    if release_invocation is not None:
                        release_invocation()

            child_runtime_factory = self.runtime.build_child_factory(
                volume_id=owner.volume_id,
                workspace_id=run.access.workspace_id,
                session_id=run.session_id,
                run_id=run.run_id,
                deadline=deadline,
                execution_output_cap=self.settings.rlm_max_execution_output_chars,
                is_authorized=lambda: not run.authority.revoked,
                semantic_child_available=bool(self.settings.daytona_child_snapshot),
            )

            async def write_child_result(call_index: int, relative_path: str, data: bytes) -> str:
                if run.authority.revoked or not isinstance(call_index, int) or call_index < 1:
                    raise ValueError("child result is no longer authorized")
                path = PurePosixPath(relative_path)
                if (
                    not relative_path
                    or path.is_absolute()
                    or ".." in path.parts
                    or "\\" in relative_path
                    or ":" in relative_path
                ):
                    raise ValueError("child result path is invalid")
                destination = str(PurePosixPath(sink.scratch_root) / "children" / str(call_index) / path)
                await sink.write_private(destination, data)
                if run.authority.revoked:
                    raise ValueError("child result is no longer authorized")
                persisted = await sink.read(destination, max_bytes=len(data))
                if sha256(persisted).digest() != sha256(data).digest():
                    raise ValueError("child result persistence checksum mismatch")
                return destination

            interpreter_factory = sandbox_interpreter_factory(
                sandbox,
                loop=asyncio.get_running_loop(),
                dispatcher=self.dispatcher,
                execution_output_cap=self.settings.rlm_max_execution_output_chars,
                run_id=run.run_id,
            )
            return RunEnvironment(
                interpreter=interpreter_factory,
                attachment_sink=sink,
                artifact_sink=sink,
                release=release_preparation,
                result_snapshot_sink=sink,
                child_runtime_factory=child_runtime_factory,
                child_result_writer=write_child_result,
                context_mount_path=sink.scratch_root,
                workspace_memory_store=host_io.memory_store,
                volume_fs=sink.volume_fs,
                session_workspace=session_workspace,
                project_workspace=project_workspace,
                resident_release=None,
                release_is_resident=False,
                history_format="sandbox",
                mark_tainted=lambda: self.runtime.mark_root_tainted(*key),
                async_bridge=self.dispatcher,
                image_identity=environment_manifest(self.sandbox_spec).digest,
            )
        except BaseException:
            self.runtime.mark_root_tainted(*key)
            if owner is not None:
                try:
                    await asyncio.shield(self.runtime.close_root_session(*key, deadline=deadline))
                except BaseException as exc:
                    logger.warning(
                        "Daytona root cleanup failed on error",
                        extra={"session_id": str(key[1]), "error_type": type(exc).__name__},
                    )
            if release_invocation is not None:
                release_invocation()
            raise

    def plan(self) -> TurnPreparationPlan:
        return TurnPreparationPlan(
            models=self.models,
            options=rlm_options(self.settings),
            recursive_options=recursive_rlm_options(self.settings),
            budget_limits=BudgetLimits(
                tool_calls=self.settings.rlm_max_tool_calls,
                recursive_children=self.settings.rlm_recursion_max_calls,
                execution_output_bytes=self.settings.rlm_max_execution_output_bytes,
                finalization_attempts=self.settings.rlm_finalization_attempts,
                finalization_seconds=self.settings.rlm_wrap_up_seconds,
            ),
            attachments=self.attachment_lifecycle,
            acquire_environment=self.acquire_environment,
            task_service=self.task_service,
            capabilities=DaytonaCapabilityPreparer(
                self.settings,
                self.skill_catalog,
                volume_paths=self.volume_paths,
                artifact_reader=self.artifact_reader,
                task_service=self.task_service,
            ),
        )
