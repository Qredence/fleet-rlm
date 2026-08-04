"""Exact prepare-before-stream boundary for one lifecycle-issued Turn."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from fleet_rlm.artifacts.promotion import RunArtifactSink
from fleet_rlm.chat.session_context import build_session_context_manifest
from fleet_rlm.chat.turn_lifecycle import ExecuteTurn
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
    PreparedCapabilities,
    RLMExecutionContext,
    RLMInterpreter,
)
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.inputs import AttachmentContextCapsule, AttachmentContextEntry
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.recursive_calls import RecursiveRLMOptions

AsyncCleanup = Callable[[], Awaitable[None]]


class TurnPreparationError(RuntimeError):
    """Base class for safe preparation failures."""


class TurnPreparationCancelledError(TurnPreparationError):
    pass


class TurnPreparationTimeoutError(TurnPreparationError):
    pass


class TurnPreparationValidationError(TurnPreparationError):
    pass


class TurnPreparationIntegrityError(TurnPreparationError):
    pass


class TurnPreparationUnavailableError(TurnPreparationError):
    pass


@dataclass(slots=True)
class _PreparedTurnResources:
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
class PreparedTurn:
    execution: RLMExecutionContext
    artifact_sink: RunArtifactSink
    _resources: _PreparedTurnResources
    result_snapshot_sink: ResultSnapshotSink | None = None

    async def aclose(self) -> None:
        await self._resources.aclose()


class TurnPreparation(Protocol):
    async def prepare(self, turn: ExecuteTurn, *, deadline: float) -> PreparedTurn: ...


@dataclass(frozen=True, slots=True)
class RunEnvironment:
    interpreter: RLMInterpreter | None
    attachment_sink: RunAttachmentSink
    artifact_sink: RunArtifactSink
    release: AsyncCleanup
    result_snapshot_sink: ResultSnapshotSink | None = None
    child_runtime_factory: ChildRuntimeFactory | None = None
    context_mount_path: str | None = None


class RunEnvironmentProvider(Protocol):
    async def acquire(self, turn: ExecuteTurn, *, deadline: float) -> RunEnvironment: ...


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
        turn: ExecuteTurn,
        environment: RunEnvironment,
        attachments: PreparedAttachments,
        *,
        deadline: float,
    ) -> PreparedCapabilities: ...


class DefaultTurnPreparer:
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

    async def prepare(self, turn: ExecuteTurn, *, deadline: float) -> PreparedTurn:
        try:
            if await turn.cancellation_requested():
                raise TurnPreparationCancelledError("Turn cancelled")
        except (DatabaseConnectionError, OSError, SQLAlchemyError) as exc:
            raise TurnPreparationUnavailableError("Turn cancellation status is unavailable") from exc

        with turn_phase_span("Turn.acquire_environment", inputs={}) as environment_phase:
            try:
                environment = await self._environments.acquire(turn, deadline=deadline)
            except TurnPreparationError:
                raise
            except Exception as exc:
                raise TurnPreparationUnavailableError("Turn environment is unavailable") from exc
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
                inputs={"attachment_count": len(turn.input.attachment_ids)},
            ) as attachments_phase:
                try:
                    staged = await self._attachments.prepare_run(
                        AttachmentAccess(turn.access.user_id, turn.access.workspace_id),
                        turn.input.attachment_ids,
                        AttachmentRun(turn.session_id, turn.run_id),
                        environment.attachment_sink,
                    )
                except (DatabaseConnectionError, OSError, SQLAlchemyError) as exc:
                    raise TurnPreparationUnavailableError("Turn attachments are unavailable") from exc
                attachments_phase.set_outputs(
                    {
                        "staged_count": len(staged.refs),
                        "staged_bytes": sum(ref.byte_size for ref in staged.refs),
                    }
                )
            with turn_phase_span(
                "Turn.prepare_capabilities",
                inputs={"skill_selection_count": len(turn.input.skill_selections)},
            ) as capabilities_phase:
                try:
                    async with asyncio.timeout_at(deadline):
                        capabilities = await self._prepare_capabilities(turn, environment, staged, deadline)
                except TimeoutError:
                    raise TurnPreparationTimeoutError("Turn preparation timed out") from None
                except (DatabaseConnectionError, OSError, SQLAlchemyError) as exc:
                    raise TurnPreparationUnavailableError("Turn capabilities are unavailable") from exc
                capabilities_phase.set_outputs({"notice_count": len(getattr(capabilities, "preparation_notices", ()))})
            try:
                if await turn.cancellation_requested():
                    raise TurnPreparationCancelledError("Turn cancelled")
            except (DatabaseConnectionError, OSError, SQLAlchemyError) as exc:
                raise TurnPreparationUnavailableError("Turn cancellation status is unavailable") from exc
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
            await asyncio.shield(_PreparedTurnResources(tuple(cleanups)).aclose())
            raise

        assert capabilities is not None

        resources = _PreparedTurnResources((environment.release, capabilities.aclose, remove_staged))
        execution = RLMExecutionContext(
            run_id=turn.run_id,
            session_id=turn.session_id,
            access=turn.access,
            request=turn.input.text,
            session_context=build_session_context_manifest(
                turn.session_id,
                turn.checkpoint_version,
                turn.history,
            ),
            models=self._models,
            options=self._options,
            deadline=deadline,
            interpreter=environment.interpreter,
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
            capabilities=capabilities,
            cancellation_requested=turn.cancellation_requested,
            preparation_notices=tuple(getattr(capabilities, "preparation_notices", ())),
            attachment_context=attachment_context,
            authority=turn.authority,
            selected_skill_count=len(turn.input.skill_selections),
            child_runtime_factory=environment.child_runtime_factory,
            recursive_options=self._recursive_options,
        )
        return PreparedTurn(
            execution,
            environment.artifact_sink,
            resources,
            environment.result_snapshot_sink,
        )

    async def _prepare_capabilities(
        self,
        turn: ExecuteTurn,
        environment: RunEnvironment,
        staged: PreparedAttachments,
        deadline: float,
    ) -> PreparedCapabilities:
        task = asyncio.create_task(self._capabilities.prepare(turn, environment, staged, deadline=deadline))
        try:
            while not task.done():
                if await turn.cancellation_requested():
                    task.cancel()
                    raise TurnPreparationCancelledError("Turn cancelled")
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    task.cancel()
                    raise TurnPreparationTimeoutError("Turn preparation timed out")
                await asyncio.wait((task,), timeout=min(0.05, remaining))
            return await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    def _check_deadline(deadline: float) -> None:
        if asyncio.get_running_loop().time() >= deadline:
            raise TurnPreparationTimeoutError("Turn preparation timed out")

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
