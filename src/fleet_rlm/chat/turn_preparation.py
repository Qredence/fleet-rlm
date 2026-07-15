"""Exact prepare-before-stream boundary for one lifecycle-issued Turn."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from fleet_rlm.artifacts.promotion import RunArtifactSink
from fleet_rlm.chat.turn_lifecycle import ExecuteTurn
from fleet_rlm.files.models import (
    AttachmentAccess,
    AttachmentRun,
    PreparedAttachment,
    PreparedAttachments,
    RunAttachmentSink,
)
from fleet_rlm.result_snapshot import ResultSnapshotSink
from fleet_rlm.rlm.context import (
    PreparedCapabilities,
    RLMExecutionContext,
    RLMHistoryMessage,
    RLMInterpreter,
)
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.model_bundle import RLMModelBundle

AsyncCleanup = Callable[[], Awaitable[None]]


class TurnPreparationError(RuntimeError):
    """Base class for safe preparation failures."""


class TurnPreparationCancelled(TurnPreparationError):
    pass


class TurnPreparationTimeout(TurnPreparationError):
    pass


class TurnPreparationValidationError(TurnPreparationError):
    pass


class TurnPreparationIntegrityError(TurnPreparationError):
    pass


class TurnPreparationUnavailable(TurnPreparationError):
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
    async def prepare(self, turn: ExecuteTurn) -> PreparedTurn: ...


@dataclass(frozen=True, slots=True)
class RunEnvironment:
    interpreter: RLMInterpreter | None
    attachment_sink: RunAttachmentSink
    artifact_sink: RunArtifactSink
    release: AsyncCleanup
    result_snapshot_sink: ResultSnapshotSink | None = None


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
    ) -> PreparedCapabilities: ...


class TurnPreparationModule:
    """Build exactly one complete immutable execution context before delivery."""

    def __init__(
        self,
        *,
        models: RLMModelBundle,
        options: RLMOptions,
        turn_timeout_seconds: int,
        attachments: RunAttachmentPreparer,
        environments: RunEnvironmentProvider,
        capabilities: CapabilityPreparer,
    ) -> None:
        self._models = models
        self._options = options
        self._turn_timeout_seconds = turn_timeout_seconds
        self._attachments = attachments
        self._environments = environments
        self._capabilities = capabilities

    async def prepare(self, turn: ExecuteTurn) -> PreparedTurn:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._turn_timeout_seconds
        if await turn.cancellation_requested():
            raise TurnPreparationCancelled("Turn cancelled")

        try:
            environment = await self._environments.acquire(turn, deadline=deadline)
        except TurnPreparationError:
            raise
        except Exception as exc:
            raise TurnPreparationUnavailable("Turn environment is unavailable") from exc

        staged = PreparedAttachments((), ())
        capabilities: PreparedCapabilities | None = None
        try:
            self._check_deadline(deadline)
            staged = await self._attachments.prepare_run(
                AttachmentAccess(turn.access.user_id, turn.access.workspace_id),
                turn.input.attachment_ids,
                AttachmentRun(turn.session_id, turn.run_id),
                environment.attachment_sink,
            )
            capabilities = await self._capabilities.prepare(turn, environment, staged)
            if await turn.cancellation_requested():
                raise TurnPreparationCancelled("Turn cancelled")
            self._check_deadline(deadline)
        except BaseException:

            async def remove_partial() -> None:
                await self._remove_staged(environment.attachment_sink, staged)

            cleanups: list[AsyncCleanup] = [environment.release]
            if capabilities is not None:
                cleanups.append(capabilities.aclose)
            cleanups.append(remove_partial)
            await asyncio.shield(_PreparedTurnResources(tuple(cleanups)).aclose())
            raise

        async def remove_staged() -> None:
            await self._remove_staged(environment.attachment_sink, staged)

        assert capabilities is not None
        resources = _PreparedTurnResources((environment.release, capabilities.aclose, remove_staged))
        execution = RLMExecutionContext(
            run_id=turn.run_id,
            session_id=turn.session_id,
            access=turn.access,
            request=turn.input.text,
            history=tuple(RLMHistoryMessage(message.role, message.content) for message in turn.history.messages),
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
            preparation_notices=(),
        )
        return PreparedTurn(
            execution,
            environment.artifact_sink,
            resources,
            environment.result_snapshot_sink,
        )

    @staticmethod
    def _check_deadline(deadline: float) -> None:
        if asyncio.get_running_loop().time() >= deadline:
            raise TurnPreparationTimeout("Turn preparation timed out")

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
