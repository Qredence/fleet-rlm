"""Atomic caller-facing Turn lifecycle and successful commit policy."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal, Protocol, TypeAlias, TypeVar
from uuid import UUID

from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactCandidate, ArtifactRef
from fleet_rlm.artifacts.promotion import ArtifactPromotion, PromotedArtifact, RunArtifactSink
from fleet_rlm.chat.run_authority import RunAuthority
from fleet_rlm.chat.turn_claim import (
    BeginSettlement,
    ClaimCommand,
    ClaimFailure,
    CompleteSettlement,
    FailClaim,
    HeartbeatClaim,
    RevokeClaim,
)
from fleet_rlm.chat.turn_detail_policy import commit_success
from fleet_rlm.result_snapshot import ResultSnapshotSink, encode_result_snapshot
from fleet_rlm.rlm.context import AsyncCancellationProbe
from fleet_rlm.rlm.dspy_contract import RLMUsage
from fleet_rlm.rlm.outcome import RLMOutcome
from fleet_rlm.sessions.committed_turn import CommittedTurn
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

logger = logging.getLogger(__name__)


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


class TurnLifecycleUnavailableError(TurnLifecycleError):
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
    authority: RunAuthority = field(default_factory=RunAuthority, compare=False, repr=False)

    @property
    def checkpoint_version(self) -> int:
        """Checkpoint from which this Turn was claimed."""
        return self._claim.base_checkpoint_version


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


def _claim_failure(failure: TurnFailure) -> ClaimFailure:
    return ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message)


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

    async def transition_claim(self, turn: ExecuteTurn, command: ClaimCommand) -> FailedRunReceipt | None: ...

    async def request_cancel(self, access: TurnAccess, run_id: UUID) -> CancelResult: ...


class TurnLifecycle(Protocol):
    heartbeat_seconds: int
    stale_after_seconds: int

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

    async def settle(self, turn: ExecuteTurn, failure: TurnFailure) -> FailedRunReceipt: ...

    async def revoke_claim(self, turn: ExecuteTurn, failure: TurnFailure) -> FailedRunReceipt: ...

    async def complete_settling(self, turn: ExecuteTurn) -> FailedRunReceipt: ...


class TurnLifecycleService:
    """Coordinate validation, Artifact publication, and atomic Turn state."""

    def __init__(
        self,
        store: _TurnStateStore,
        *,
        max_artifact_bytes: int,
        heartbeat_seconds: int = 10,
        stale_after_seconds: int = 60,
    ) -> None:
        self._store = store
        self._promotion = ArtifactPromotion(max_bytes=max_artifact_bytes)
        self._max_artifact_bytes = max_artifact_bytes
        self.heartbeat_seconds = heartbeat_seconds
        self.stale_after_seconds = stale_after_seconds

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
        if turn.authority.revoked:
            raise TurnLifecycleUnavailableError("Turn claim is no longer available")
        if isinstance(resolution, TurnFailure):
            return await self._transition_receipt(turn, FailClaim(_claim_failure(resolution), resolution.usage))
        if not resolution.succeeded:
            await self._rollback(
                artifact_sink,
                (candidate.staging_path for candidate in resolution.artifact_candidates),
            )
            status = resolution.terminal_status
            if status == "completed":
                raise TurnStateError("contradictory successful outcome state")
            failure = TurnFailure(
                terminal_status=status,
                failure_code=failure_code_for_terminal_status(status),
                public_message=resolution.public_error_message or "Turn failed",
                usage=resolution.usage,
            )
            return await self._transition_receipt(turn, FailClaim(_claim_failure(failure), failure.usage))

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
        stage = "read_candidates"
        try:
            validated = await self._read_candidates(candidates, artifact_sink)
            stage = "publish_artifacts"
            promoted = await self._publish(candidates, validated, artifact_sink, written)
            stage = "build_committed_turn"
            committed = commit_success(resolution, tuple(item.ref for item in promoted))
            if result_snapshot_sink is not None:
                prediction = resolution.prediction
                if prediction is None:
                    raise TurnStateError("successful outcome requires a prediction")
                stage = "encode_result_snapshot"
                snapshot_path = result_snapshot_sink.result_path(turn.session_id, turn.run_id)
                snapshot = encode_result_snapshot(
                    turn.session_id,
                    turn.run_id,
                    prediction,
                    resolution.usage,
                )
                stage = "write_result_snapshot"
                snapshot_write, write_cancelled = await _settle_owned(
                    result_snapshot_sink.write(snapshot_path, snapshot)
                )
                snapshot_write.result()
                if write_cancelled:
                    raise asyncio.CancelledError
            stage = "commit_turn"
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
            cleanup_cancelled |= await self._rollback(
                artifact_sink,
                (candidate.staging_path for candidate in candidates),
            )
            if isinstance(exc, asyncio.CancelledError) or cleanup_cancelled:
                raise asyncio.CancelledError from None
            if not isinstance(exc, Exception):
                raise
            logger.exception(
                "Turn finalization failed stage=%s session_id=%s run_id=%s",
                stage,
                turn.session_id,
                turn.run_id,
            )
            failure = TurnFailure(
                "failed",
                "commit_failed",
                "Turn could not be committed",
                resolution.usage,
            )
            return await self._transition_receipt(turn, FailClaim(_claim_failure(failure), failure.usage))

        await self._rollback(artifact_sink, (candidate.staging_path for candidate in candidates))
        return receipt

    async def request_cancel(self, access: TurnAccess, run_id: UUID) -> CancelResult:
        return await self._store.request_cancel(access, run_id)

    async def heartbeat(self, turn: ExecuteTurn) -> None:
        await self._transition(turn, HeartbeatClaim())

    async def settle(self, turn: ExecuteTurn, failure: TurnFailure) -> FailedRunReceipt:
        """Revoke commit authority while retaining the durable claim for cleanup."""
        return await self._transition_receipt(turn, BeginSettlement(_claim_failure(failure), failure.usage))

    async def revoke_claim(self, turn: ExecuteTurn, failure: TurnFailure) -> FailedRunReceipt:
        """Idempotently classify a Run whose durable claim authority was lost."""
        return await self._transition_receipt(turn, RevokeClaim(_claim_failure(failure), failure.usage))

    async def complete_settling(self, turn: ExecuteTurn) -> FailedRunReceipt:
        """Release a retained claim only after owned cleanup has completed."""
        return await self._transition_receipt(turn, CompleteSettlement())

    async def _transition(self, turn: ExecuteTurn, command: ClaimCommand) -> FailedRunReceipt | None:
        return await self._store.transition_claim(turn, command)

    async def _transition_receipt(self, turn: ExecuteTurn, command: ClaimCommand) -> FailedRunReceipt:
        receipt = await self._transition(turn, command)
        if receipt is None:
            raise TurnStateError("claim transition did not return a receipt")
        return receipt

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
