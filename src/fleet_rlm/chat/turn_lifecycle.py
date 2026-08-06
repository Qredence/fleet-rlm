"""Atomic caller-facing Turn lifecycle and successful commit policy."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal, Protocol, TypeAlias, TypeVar
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
from fleet_rlm.chat.turn_cleanup import TurnCleanupSupervisor, TurnCleanupUnavailableError
from fleet_rlm.chat.turn_detail_policy import commit_success
from fleet_rlm.observability.turn_tracing import turn_phase_span
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
        cleanup: TurnCleanupSupervisor | None = None,
    ) -> None:
        self._store = store
        self._promotion = ArtifactPromotion(max_bytes=max_artifact_bytes)
        self._max_artifact_bytes = max_artifact_bytes
        self.heartbeat_seconds = heartbeat_seconds
        self.stale_after_seconds = stale_after_seconds
        self._cleanup = cleanup

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
        """
        Finalize a turn with a successful outcome or record its failure.

        Parameters:
            turn (ExecuteTurn): The claimed turn being finalized.
            resolution (RLMOutcome | TurnFailure): The execution outcome or failure to record.
            artifact_sink (RunArtifactSink | None): Storage for staged and promoted artifacts.
            result_snapshot_sink (ResultSnapshotSink | None): Storage for the optional result snapshot.

        Returns:
            TurnFinalization: The receipt for the committed turn or recorded failure.

        Raises:
            TurnLifecycleUnavailableError: If the turn claim has been revoked.
            TurnStateError: If the outcome contains an invalid state.
            TurnValidationError: If artifacts are provided without an artifact sink.
        """
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
        snapshot_task: asyncio.Task[Any] | None = None
        stage = "read_candidates"
        try:
            validated = await self._read_candidates(candidates, artifact_sink)
            stage = "publish_artifacts"
            promoted = await self._publish(candidates, validated, artifact_sink, written)
            if turn.authority.revoked:
                raise TurnLifecycleUnavailableError("Turn claim is no longer available")
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
                # Start the snapshot write before the commit so the volume
                # round-trip overlaps with the DB transaction; reconciled after
                # the commit is durable (see _reconcile_snapshot_after_commit).
                snapshot_task = asyncio.ensure_future(result_snapshot_sink.write(snapshot_path, snapshot))
            stage = "commit_turn"
            if turn.authority.revoked:
                raise TurnLifecycleUnavailableError("Turn claim is no longer available")
            commit_task, commit_cancelled = await _settle_owned(self._store.commit(turn, committed, promoted))
            try:
                receipt = commit_task.result()
            except BaseException:
                if commit_cancelled:
                    raise asyncio.CancelledError from None
                raise
        except BaseException as exc:
            if snapshot_task is not None:
                # Never let an in-flight snapshot write race rollback removals.
                await _settle_owned(snapshot_task)
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

        await self._reconcile_snapshot_after_commit(snapshot_task, result_snapshot_sink, snapshot_path)
        await self._settle_staging(artifact_sink, candidates)
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
        with turn_phase_span("Turn.claim_transition", inputs={"command": type(command).__name__}):
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
        # Independent volume reads run concurrently; integrity validation stays
        # sequential and ordered so the first bad candidate still fails fast.
        reads = [
            asyncio.ensure_future(sink.read(candidate.staging_path, max_bytes=self._max_artifact_bytes))
            for candidate in candidates
        ]
        settled = [await _settle_owned(read) for read in reads]
        values: list[bytes] = []
        cancellation_requested = False
        for candidate, (read_task, read_cancelled) in zip(candidates, settled, strict=True):
            cancellation_requested |= read_cancelled
            try:
                data = read_task.result()
            except BaseException:
                if read_cancelled:
                    raise asyncio.CancelledError from None
                raise
            if len(data) != candidate.byte_size or sha256(data).hexdigest() != candidate.checksum_sha256.lower():
                raise TurnIntegrityError("Artifact Candidate bytes failed integrity validation")
            values.append(data)
        if cancellation_requested:
            raise asyncio.CancelledError
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
    async def _reconcile_snapshot_after_commit(
        snapshot_task: asyncio.Task[Any] | None,
        sink: ResultSnapshotSink | None,
        snapshot_path: str | None,
    ) -> None:
        """Settle the overlapped result-snapshot write after the commit is durable.

        The snapshot is a read cache, never referenced by the committed state:
        its failure must not roll back a committed Turn. Post-commit errors are
        logged and any partial snapshot bytes are removed best-effort.
        """
        if snapshot_task is None:
            return
        settled, cancelled = await _settle_owned(snapshot_task)
        try:
            settled.result()
        except asyncio.CancelledError:
            logger.warning("result snapshot write cancelled after commit; Turn remains committed")
        except Exception:
            logger.warning("result snapshot write failed after commit; Turn remains committed", exc_info=True)
            if sink is not None and snapshot_path is not None:
                await TurnLifecycleService._rollback(sink, (snapshot_path,))
        else:
            if cancelled:
                logger.warning("result snapshot write saw cancellation after commit; Turn remains committed")

    async def _settle_staging(
        self,
        sink: RunArtifactSink | None,
        candidates: tuple[ArtifactCandidate, ...],
    ) -> None:
        """Remove staging candidates, detached when a cleanup supervisor is available."""
        if self._cleanup is not None and sink is not None and candidates:
            staging_paths = tuple(candidate.staging_path for candidate in candidates)

            async def _remove_staging() -> None:
                await self._rollback(sink, staging_paths)

            try:
                self._cleanup.submit(_remove_staging())
                return
            except TurnCleanupUnavailableError:
                logger.warning("Turn cleanup capacity unavailable; settling staging inline")
        await self._rollback(sink, (candidate.staging_path for candidate in candidates))

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
