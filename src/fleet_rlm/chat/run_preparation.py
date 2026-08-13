"""Exact prepare-before-stream boundary for one lifecycle-issued Run."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from fleet_rlm.artifacts.promotion import RunArtifactSink
from fleet_rlm.chat.post_commit_memory import OwnedPostCommitMemoryPromotion
from fleet_rlm.chat.run_lifecycle import ClaimedRun
from fleet_rlm.chat.session_context import build_session_context_manifest
from fleet_rlm.files.memory_models import WORKSPACE_MEMORY_INJECTION_TAIL_BYTES
from fleet_rlm.files.models import (
    AttachmentAccess,
    AttachmentRun,
    PreparedAttachment,
    PreparedAttachments,
    RunAttachmentSink,
)
from fleet_rlm.observability.turn_tracing import turn_phase_span
from fleet_rlm.persistence.database import DatabaseConnectionError
from fleet_rlm.result_snapshot import ResultSnapshotSink
from fleet_rlm.rlm.child_runtime import ChildRuntimeFactory
from fleet_rlm.rlm.context import (
    DelegationPolicy,
    ExecutionRuntime,
    PreparedCapabilities,
    RLMExecutionContext,
    RLMInterpreter,
    RunIdentity,
    SessionView,
)
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.inputs import AttachmentContextCapsule, AttachmentContextEntry
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.recursive_calls import RecursiveRLMOptions

AsyncCleanup = Callable[[], Awaitable[None]]


class RunPreparationError(RuntimeError):
    """Base class for safe preparation failures."""


class RunPreparationCancelledError(RunPreparationError):
    pass


class RunPreparationTimeoutError(RunPreparationError):
    pass


class RunPreparationValidationError(RunPreparationError):
    pass


class RunPreparationIntegrityError(RunPreparationError):
    pass


class RunPreparationUnavailableError(RunPreparationError):
    pass


@dataclass(slots=True)
class _PreparedRunResources:
    cleanups: tuple[AsyncCleanup, ...]
    _closed: bool = field(default=False, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            for cleanup in reversed(self.cleanups):
                try:
                    await cleanup()
                except Exception:
                    continue


@dataclass(frozen=True, slots=True)
class PreparedRun:
    execution: RLMExecutionContext
    artifact_sink: RunArtifactSink
    _resources: _PreparedRunResources
    result_snapshot_sink: ResultSnapshotSink | None = None
    post_commit_memory_promotion: OwnedPostCommitMemoryPromotion | None = None

    async def aclose(self) -> None:
        if self.post_commit_memory_promotion is not None:
            await self.post_commit_memory_promotion.wait_owned()
        await self._resources.aclose()


def _workspace_memory_digest(capabilities: PreparedCapabilities) -> str:
    """Bounded defensive projection of the capability digest; never fails a Run."""
    digest = getattr(capabilities, "workspace_memory_digest", "")
    if not isinstance(digest, str) or len(digest.encode("utf-8")) > WORKSPACE_MEMORY_INJECTION_TAIL_BYTES:
        return ""
    return digest


class RunPreparation(Protocol):
    async def prepare(self, run: ClaimedRun, *, deadline: float) -> PreparedRun: ...


@dataclass(frozen=True, slots=True)
class RunEnvironment:
    interpreter: RLMInterpreter | None
    attachment_sink: RunAttachmentSink
    artifact_sink: RunArtifactSink
    release: AsyncCleanup
    result_snapshot_sink: ResultSnapshotSink | None = None
    child_runtime_factory: ChildRuntimeFactory | None = None
    context_mount_path: str | None = None
    workspace_memory_store: Any | None = None
    post_commit_memory_promotion: OwnedPostCommitMemoryPromotion | None = None


class RunEnvironmentProvider(Protocol):
    async def acquire(self, run: ClaimedRun, *, deadline: float) -> RunEnvironment: ...


class RunAttachmentPreparer(Protocol):
    async def prepare_run(
        self,
        access: AttachmentAccess,
        attachment_ids: Sequence[UUID],
        run: AttachmentRun,
        sink: RunAttachmentSink,
    ) -> PreparedAttachments: ...


class CapabilityPreparer(Protocol):
    async def prepare(
        self,
        run: ClaimedRun,
        environment: RunEnvironment,
        attachments: PreparedAttachments,
        *,
        deadline: float,
    ) -> PreparedCapabilities: ...


class DefaultRunPreparer:
    """Build exactly one complete immutable execution context before delivery."""

    def __init__(
        self,
        *,
        models: RLMModelBundle,
        options: RLMOptions,
        attachments: RunAttachmentPreparer,
        environments: RunEnvironmentProvider,
        capabilities: CapabilityPreparer,
        recursive_options: RecursiveRLMOptions | None = None,
    ) -> None:
        self._models = models
        self._options = options
        self._attachments = attachments
        self._environments = environments
        self._capabilities = capabilities
        self._recursive_options = recursive_options or RecursiveRLMOptions()

    async def prepare(self, run: ClaimedRun, *, deadline: float) -> PreparedRun:
        """
        Prepare the execution context and resources required to run a Run.

        Parameters:
            run (ClaimedRun): Run request and execution metadata.
            deadline (float): Absolute deadline for Run preparation.

        Returns:
            PreparedRun: Prepared execution context, artifact sinks, and managed resources.

        Raises:
            RunPreparationCancelledError: If the Run is cancelled.
            RunPreparationTimeoutError: If Run preparation exceeds the deadline.
            RunPreparationUnavailableError: If required preparation services or resources are unavailable.
        """
        try:
            if await run.cancellation_requested():
                raise RunPreparationCancelledError("Turn cancelled")
        except (DatabaseConnectionError, OSError, SQLAlchemyError) as exc:
            raise RunPreparationUnavailableError("Turn cancellation status is unavailable") from exc

        with turn_phase_span("Turn.acquire_environment", inputs={}) as environment_phase:
            try:
                environment = await self._environments.acquire(run, deadline=deadline)
            except RunPreparationError:
                raise
            except Exception as exc:
                raise RunPreparationUnavailableError("Turn environment is unavailable") from exc
            environment_phase.set_outputs(
                {
                    "has_interpreter": environment.interpreter is not None,
                    "has_snapshot_sink": environment.result_snapshot_sink is not None,
                }
            )

        staged = PreparedAttachments((), ())
        capabilities: PreparedCapabilities | None = None

        async def remove_staged() -> None:
            await self._remove_staged(environment.attachment_sink, staged)

        try:
            self._check_deadline(deadline)
            with turn_phase_span(
                "Turn.stage_attachments",
                inputs={"attachment_count": len(run.input.attachment_ids)},
            ) as attachments_phase:
                try:
                    staged = await self._attachments.prepare_run(
                        AttachmentAccess(run.access.user_id, run.access.workspace_id),
                        run.input.attachment_ids,
                        AttachmentRun(run.session_id, run.run_id),
                        environment.attachment_sink,
                    )
                except (DatabaseConnectionError, OSError, SQLAlchemyError) as exc:
                    raise RunPreparationUnavailableError("Turn attachments are unavailable") from exc
                attachments_phase.set_outputs(
                    {
                        "staged_count": len(staged.refs),
                        "staged_bytes": sum(ref.byte_size for ref in staged.refs),
                    }
                )
            with turn_phase_span(
                "Turn.prepare_capabilities",
                inputs={"skill_selection_count": len(run.input.skill_selections)},
            ) as capabilities_phase:
                try:
                    async with asyncio.timeout_at(deadline):
                        capabilities = await self._prepare_capabilities(run, environment, staged, deadline)
                except TimeoutError:
                    raise RunPreparationTimeoutError("Turn preparation timed out") from None
                except (DatabaseConnectionError, OSError, SQLAlchemyError) as exc:
                    raise RunPreparationUnavailableError("Turn capabilities are unavailable") from exc
                capabilities_phase.set_outputs({"notice_count": len(getattr(capabilities, "preparation_notices", ()))})
            try:
                if await run.cancellation_requested():
                    raise RunPreparationCancelledError("Turn cancelled")
            except (DatabaseConnectionError, OSError, SQLAlchemyError) as exc:
                raise RunPreparationUnavailableError("Turn cancellation status is unavailable") from exc
            self._check_deadline(deadline)

            staged_by_id = {item.attachment_id: item for item in staged.staged}
            attachment_context = None
            if staged.refs and environment.context_mount_path is not None:
                attachment_context = AttachmentContextCapsule(
                    tuple(
                        AttachmentContextEntry(
                            attachment_id=ref.id,
                            filename=ref.filename,
                            content_type=ref.content_type,
                            byte_size=ref.byte_size,
                            checksum_sha256=ref.checksum_sha256,
                            sandbox_path=staged_by_id[ref.id].sandbox_path,
                        )
                        for ref in staged.refs
                    ),
                    mount_root=environment.context_mount_path,
                )
        except BaseException:
            cleanups: list[AsyncCleanup] = [environment.release]
            if capabilities is not None:
                cleanups.append(capabilities.aclose)
            cleanups.append(remove_staged)
            await asyncio.shield(_PreparedRunResources(tuple(cleanups)).aclose())
            raise

        assert capabilities is not None

        resources = _PreparedRunResources((environment.release, capabilities.aclose, remove_staged))
        execution = RLMExecutionContext(
            identity=RunIdentity(
                run_id=run.run_id,
                session_id=run.session_id,
                access=run.access,
                authority=run.authority,
            ),
            session=SessionView(
                request=run.input.text,
                session_context=build_session_context_manifest(
                    run.session_id,
                    run.checkpoint_version,
                    run.history,
                ),
                attachments=tuple(
                    PreparedAttachment(
                        ref.id,
                        ref.filename,
                        ref.content_type,
                        ref.byte_size,
                        ref.checksum_sha256,
                    )
                    for ref in staged.refs
                ),
                attachment_context=attachment_context,
                preparation_notices=tuple(getattr(capabilities, "preparation_notices", ())),
                workspace_memory_digest=_workspace_memory_digest(capabilities),
            ),
            execution=ExecutionRuntime(
                models=self._models,
                options=self._options,
                interpreter=environment.interpreter,
                cancellation_requested=run.cancellation_requested,
                deadline=deadline,
            ),
            capabilities=capabilities,
            delegation=DelegationPolicy(
                child_runtime_factory=environment.child_runtime_factory,
                recursive_options=self._recursive_options,
            ),
            selected_skill_count=len(run.input.skill_selections),
        )
        return PreparedRun(
            execution,
            environment.artifact_sink,
            resources,
            environment.result_snapshot_sink,
            environment.post_commit_memory_promotion,
        )

    async def _prepare_capabilities(
        self,
        run: ClaimedRun,
        environment: RunEnvironment,
        staged: PreparedAttachments,
        deadline: float,
    ) -> PreparedCapabilities:
        task = asyncio.create_task(self._capabilities.prepare(run, environment, staged, deadline=deadline))
        try:
            while not task.done():
                if await run.cancellation_requested():
                    task.cancel()
                    raise RunPreparationCancelledError("Turn cancelled")
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    task.cancel()
                    raise RunPreparationTimeoutError("Turn preparation timed out")
                await asyncio.wait((task,), timeout=min(0.05, remaining))
            return await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    def _check_deadline(deadline: float) -> None:
        if asyncio.get_running_loop().time() >= deadline:
            raise RunPreparationTimeoutError("Turn preparation timed out")

    @staticmethod
    async def _remove_staged(
        sink: RunAttachmentSink,
        prepared: PreparedAttachments,
    ) -> None:
        for item in reversed(prepared.staged):
            try:
                await sink.remove_private(item.sandbox_path)
            except Exception:
                continue


__all__ = [
    "AsyncCleanup",
    "CapabilityPreparer",
    "DefaultRunPreparer",
    "PreparedRun",
    "RunAttachmentPreparer",
    "RunEnvironment",
    "RunEnvironmentProvider",
    "RunPreparation",
    "RunPreparationCancelledError",
    "RunPreparationError",
    "RunPreparationIntegrityError",
    "RunPreparationTimeoutError",
    "RunPreparationUnavailableError",
    "RunPreparationValidationError",
]
