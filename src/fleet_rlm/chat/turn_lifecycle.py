"""Atomic caller-facing Turn lifecycle and successful commit policy."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, Protocol, TypeAlias, TypeVar
from uuid import UUID

from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactCandidate, ArtifactRef
from fleet_rlm.artifacts.promotion import ArtifactPromotion, PromotedArtifact, RunArtifactSink
from fleet_rlm.chat.turn_detail_policy import commit_success
from fleet_rlm.result_snapshot import ResultSnapshotSink, encode_result_snapshot
from fleet_rlm.rlm.context import AsyncCancellationProbe
from fleet_rlm.rlm.dspy_contract import RLMUsage
from fleet_rlm.rlm.outcome import RLMOutcome
from fleet_rlm.sessions.committed_turn import CommittedTurn
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput


class TurnLifecycleError(RuntimeError):
    """Base class for safe lifecycle failures."""


class TurnNotFoundError(TurnLifecycleError):
    pass


class TurnInProgressError(TurnLifecycleError):
    pass


class TurnIdempotencyMismatchError(TurnLifecycleError):
    pass


class TurnValidationError(TurnLifecycleError):
    pass


class TurnStateError(TurnLifecycleError):
    pass


class TurnIntegrityError(TurnLifecycleError):
    pass


class TurnLifecycleUnavailable(TurnLifecycleError):
    pass


_T = TypeVar("_T")


async def _settle_owned(awaitable: Awaitable[_T]) -> tuple[asyncio.Future[_T], bool]:
    """Shield one owned side effect and wait through repeated caller cancellation."""
    task = asyncio.ensure_future(awaitable)
    cancellation_requested = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                return task, cancellation_requested
            cancellation_requested = True
        except BaseException:
            return task, cancellation_requested
    return task, cancellation_requested


@dataclass(frozen=True, slots=True)
class BeginTurn:
    access: TurnAccess
    session_id: UUID
    input: TurnInput
    idempotency_key: str
    proposed_run_id: UUID


@dataclass(frozen=True, slots=True)
class _TurnClaimToken:
    value: UUID
    base_checkpoint_version: int = 0


@dataclass(frozen=True, slots=True)
class ExecuteTurn:
    run_id: UUID
    session_id: UUID
    access: TurnAccess
    input: TurnInput
    history: SessionHistory
    cancellation_requested: AsyncCancellationProbe
    _claim: _TurnClaimToken


@dataclass(frozen=True, slots=True)
class ReplayTurn:
    run_id: UUID
    session_id: UUID
    committed_turn: CommittedTurn
    checkpoint_version: int


TurnStart: TypeAlias = ExecuteTurn | ReplayTurn
FailureCode: TypeAlias = Literal[
    "preparation_failed",
    "execution_failed",
    "commit_failed",
    "cancelled",
    "timeout",
    "stale_claim",
]


def failure_code_for_terminal_status(
    status: Literal["failed", "cancelled", "timeout"],
) -> FailureCode:
    if status == "cancelled":
        return "cancelled"
    if status == "timeout":
        return "timeout"
    return "execution_failed"


@dataclass(frozen=True, slots=True)
class TurnFailure:
    terminal_status: Literal["failed", "cancelled", "timeout"]
    failure_code: FailureCode
    public_message: str
    usage: RLMUsage


@dataclass(frozen=True, slots=True)
class CommittedTurnReceipt:
    run_id: UUID
    checkpoint_version: int
    committed_turn: CommittedTurn
    artifacts: tuple[ArtifactRef, ...]


@dataclass(frozen=True, slots=True)
class FailedRunReceipt:
    run_id: UUID
    terminal_status: Literal["failed", "cancelled", "timeout"]
    failure_code: FailureCode
    public_message: str
    durable: bool


TurnFinalization: TypeAlias = CommittedTurnReceipt | FailedRunReceipt
CancelResult: TypeAlias = Literal["requested", "already_requested", "already_terminal"]


class _TurnStateStore(Protocol):
    async def begin(self, request: BeginTurn) -> TurnStart: ...

    async def commit(
        self,
        turn: ExecuteTurn,
        committed: CommittedTurn,
        artifacts: tuple[PromotedArtifact, ...],
    ) -> CommittedTurnReceipt: ...

    async def fail(self, turn: ExecuteTurn, failure: TurnFailure) -> FailedRunReceipt: ...

    async def request_cancel(self, access: TurnAccess, run_id: UUID) -> CancelResult: ...

    async def heartbeat(self, turn: ExecuteTurn) -> None: ...


class TurnLifecycle(Protocol):
    async def begin(self, request: BeginTurn) -> TurnStart: ...

    async def finish(
        self,
        turn: ExecuteTurn,
        resolution: RLMOutcome | TurnFailure,
        *,
        artifact_sink: RunArtifactSink | None = None,
        result_snapshot_sink: ResultSnapshotSink | None = None,
    ) -> TurnFinalization: ...

    async def request_cancel(self, access: TurnAccess, run_id: UUID) -> CancelResult: ...

    async def heartbeat(self, turn: ExecuteTurn) -> None: ...


class TurnLifecycleModule:
    """Coordinate validation, Artifact publication, and atomic Turn state."""

    def __init__(
        self,
        store: _TurnStateStore,
        *,
        max_artifact_bytes: int,
        heartbeat_seconds: int = 10,
    ) -> None:
        self._store = store
        self._promotion = ArtifactPromotion(max_bytes=max_artifact_bytes)
        self._max_artifact_bytes = max_artifact_bytes
        self.heartbeat_seconds = heartbeat_seconds

    async def begin(self, request: BeginTurn) -> TurnStart:
        return await self._store.begin(request)

    async def finish(
        self,
        turn: ExecuteTurn,
        resolution: RLMOutcome | TurnFailure,
        *,
        artifact_sink: RunArtifactSink | None = None,
        result_snapshot_sink: ResultSnapshotSink | None = None,
    ) -> TurnFinalization:
        if isinstance(resolution, TurnFailure):
            return await self._store.fail(turn, resolution)
        if not resolution.succeeded:
            status = resolution.terminal_status
            if status == "completed":
                raise TurnStateError("contradictory successful outcome state")
            return await self._store.fail(
                turn,
                TurnFailure(
                    terminal_status=status,
                    failure_code=failure_code_for_terminal_status(status),
                    public_message=resolution.public_error_message or "Turn failed",
                    usage=resolution.usage,
                ),
            )

        candidates = self._promotion.validate(
            resolution.artifact_candidates,
            access=ArtifactAccess(turn.access.user_id, turn.access.workspace_id),
            session_id=turn.session_id,
            run_id=turn.run_id,
        )
        if candidates and artifact_sink is None:
            raise TurnValidationError("Artifact Candidates require a Run Artifact sink")

        written: list[str] = []
        snapshot_path: str | None = None
        try:
            validated = await self._read_candidates(candidates, artifact_sink)
            promoted = await self._publish(candidates, validated, artifact_sink, written)
            committed = commit_success(resolution, tuple(item.ref for item in promoted))
            if result_snapshot_sink is not None:
                prediction = resolution.prediction
                if prediction is None:
                    raise TurnStateError("successful outcome requires a prediction")
                snapshot_path = result_snapshot_sink.result_path(turn.session_id, turn.run_id)
                snapshot = encode_result_snapshot(
                    turn.session_id,
                    turn.run_id,
                    prediction,
                    resolution.usage,
                )
                snapshot_write, write_cancelled = await _settle_owned(
                    result_snapshot_sink.write(snapshot_path, snapshot)
                )
                snapshot_write.result()
                if write_cancelled:
                    raise asyncio.CancelledError
            commit_task, commit_cancelled = await _settle_owned(self._store.commit(turn, committed, promoted))
            try:
                receipt = commit_task.result()
            except BaseException:
                if commit_cancelled:
                    raise asyncio.CancelledError from None
                raise
        except BaseException as exc:
            cleanup_cancelled = False
            if snapshot_path is not None:
                cleanup_cancelled |= await self._rollback(result_snapshot_sink, (snapshot_path,))
            cleanup_cancelled |= await self._rollback(artifact_sink, reversed(written))
            if isinstance(exc, asyncio.CancelledError) or cleanup_cancelled:
                raise asyncio.CancelledError from None
            if not isinstance(exc, Exception):
                raise
            failure = TurnFailure(
                "failed",
                "commit_failed",
                "Turn could not be committed",
                resolution.usage,
            )
            return await self._store.fail(turn, failure)

        await self._rollback(artifact_sink, (candidate.staging_path for candidate in candidates))
        return receipt

    async def request_cancel(self, access: TurnAccess, run_id: UUID) -> CancelResult:
        return await self._store.request_cancel(access, run_id)

    async def heartbeat(self, turn: ExecuteTurn) -> None:
        await self._store.heartbeat(turn)

    async def _read_candidates(
        self,
        candidates: tuple[ArtifactCandidate, ...],
        sink: RunArtifactSink | None,
    ) -> tuple[bytes, ...]:
        if sink is None:
            return ()
        values: list[bytes] = []
        for candidate in candidates:
            data = await sink.read(candidate.staging_path, max_bytes=self._max_artifact_bytes)
            if len(data) != candidate.byte_size or sha256(data).hexdigest() != candidate.checksum_sha256.lower():
                raise TurnIntegrityError("Artifact Candidate bytes failed integrity validation")
            values.append(data)
        return tuple(values)

    @staticmethod
    async def _publish(
        candidates: tuple[ArtifactCandidate, ...],
        values: tuple[bytes, ...],
        sink: RunArtifactSink | None,
        written: list[str],
    ) -> tuple[PromotedArtifact, ...]:
        if sink is None:
            return ()
        artifacts: list[PromotedArtifact] = []
        for candidate, data in zip(candidates, values, strict=True):
            written.append(candidate.durable_path)
            write_task, write_cancelled = await _settle_owned(sink.write(candidate.durable_path, data))
            write_task.result()
            if write_cancelled:
                raise asyncio.CancelledError
            artifacts.append(
                PromotedArtifact(
                    ArtifactRef(
                        candidate.id,
                        candidate.session_id,
                        candidate.run_id,
                        candidate.kind,
                        candidate.title,
                        candidate.media_type,
                        candidate.byte_size,
                        candidate.checksum_sha256,
                    ),
                    candidate.durable_path,
                )
            )
        return tuple(artifacts)

    @staticmethod
    async def _rollback(
        sink: RunArtifactSink | ResultSnapshotSink | None,
        locations,
    ) -> bool:
        if sink is None:
            return False
        cancellation_requested = False
        for location in locations:
            try:
                remove_task, remove_cancelled = await _settle_owned(sink.remove(location))
                cancellation_requested |= remove_cancelled
                remove_task.result()
            except asyncio.CancelledError:
                cancellation_requested = True
            except Exception:
                continue
        return cancellation_requested
