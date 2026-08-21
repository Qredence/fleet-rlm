"""Atomic Run lifecycle and successful commit policy for one public Turn."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal, Protocol, TypeAlias
from uuid import UUID

from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactCandidate, ArtifactRef
from fleet_rlm.artifacts.promotion import ArtifactPromotion, PromotedArtifact, RunArtifactSink
from fleet_rlm.chat.post_commit_memory import OwnedPostCommitMemoryPromotion
from fleet_rlm.chat.run_authority import RunAuthority
from fleet_rlm.chat.run_claim import (
    BeginSettlement,
    ClaimCommand,
    ClaimFailure,
    ClaimFailureCode,
    CompleteSettlement,
    FailClaim,
    HeartbeatClaim,
    RevokeClaim,
    failure_code_for_terminal_status,
)
from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor, RunCleanupUnavailableError
from fleet_rlm.chat.turn_detail_policy import commit_success
from fleet_rlm.files.memory_candidates import (
    OUTCOME_DEADLINE_EXCEEDED,
    OUTCOME_INTERRUPTED,
    OUTCOME_PROMOTED,
    OUTCOME_PROMOTION_FAILED,
    MemoryCandidate,
    MemoryPromotionIntent,
)
from fleet_rlm.observability.turn_tracing import turn_phase_span
from fleet_rlm.result_snapshot import ResultSnapshotSink, encode_result_snapshot
from fleet_rlm.rlm.context import AsyncCancellationProbe
from fleet_rlm.rlm.dspy_contract import RLMUsage
from fleet_rlm.rlm.outcome import RLMOutcome
from fleet_rlm.runtime.owned_effect import OwnedEffect
from fleet_rlm.sessions.committed_turn import CommittedTurn
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

MemoryIntentBuilder = Callable[[UUID, tuple[MemoryCandidate, ...]], tuple[MemoryPromotionIntent, ...]]

logger = logging.getLogger(__name__)
_POST_COMMIT_MEMORY_PROMOTION_TIMEOUT_S = 2.0


class RunLifecycleError(RuntimeError):
    """Base class for safe lifecycle failures."""


class RunNotFoundError(RunLifecycleError):
    pass


class RunInProgressError(RunLifecycleError):
    pass


class RunIdempotencyMismatchError(RunLifecycleError):
    pass


class RunValidationError(RunLifecycleError):
    pass


class RunStateError(RunLifecycleError):
    pass


class RunAlreadyCompletedError(RunStateError):
    """The Run already committed; late claim work targeting it is a benign no-op."""


class RunIntegrityError(RunLifecycleError):
    pass


class RunLifecycleUnavailableError(RunLifecycleError):
    pass


@dataclass(frozen=True, slots=True)
class RunClaim:
    """Validated Turn claim request passed to the run state store to begin a new Run."""

    access: TurnAccess
    session_id: UUID
    input: TurnInput
    idempotency_key: str
    proposed_run_id: UUID


@dataclass(frozen=True, slots=True)
class _RunClaimToken:
    value: UUID
    base_checkpoint_version: int = 0


@dataclass(frozen=True, slots=True)
class ClaimedRun:
    """Active Run that holds a durable claim and may be committed or failed.

    Callers obtain this through ``RunLifecycle.begin``.  The embedded
    ``RunAuthority`` is the shared revocation signal: the heartbeat background
    task, execution driver, and lifecycle all check ``authority.revoked`` to
    coordinate orderly teardown without a separate cancel event.
    """

    run_id: UUID
    session_id: UUID
    access: TurnAccess
    input: TurnInput
    history: SessionHistory
    cancellation_requested: AsyncCancellationProbe
    _claim: _RunClaimToken
    authority: RunAuthority = field(default_factory=RunAuthority, compare=False, repr=False)

    @property
    def checkpoint_version(self) -> int:
        """Checkpoint from which this Turn was claimed."""
        return self._claim.base_checkpoint_version


@dataclass(frozen=True, slots=True)
class CommittedRunReplay:
    run_id: UUID
    session_id: UUID
    committed_turn: CommittedTurn
    checkpoint_version: int


RunStart: TypeAlias = ClaimedRun | CommittedRunReplay
# The failure-code vocabulary is owned once by run_claim; lifecycle aliases it.
RunFailureCode: TypeAlias = ClaimFailureCode


@dataclass(frozen=True, slots=True)
class RunFailure:
    """Typed failure record carrying public classification, message, and usage for durable settlement."""

    terminal_status: Literal["failed", "cancelled", "timeout"]
    failure_code: RunFailureCode
    public_message: str
    usage: RLMUsage


def _claim_failure(failure: RunFailure) -> ClaimFailure:
    return ClaimFailure(failure.terminal_status, failure.failure_code, failure.public_message)


@dataclass(frozen=True, slots=True)
class CommittedTurnReceipt:
    """Durable receipt for a successfully committed Turn, including its promoted artifacts."""

    run_id: UUID
    checkpoint_version: int
    committed_turn: CommittedTurn
    artifacts: tuple[ArtifactRef, ...]


@dataclass(frozen=True, slots=True)
class FailedRunReceipt:
    """Durable receipt for a Run that ended in failure, cancellation, or timeout."""

    run_id: UUID
    terminal_status: Literal["failed", "cancelled", "timeout"]
    failure_code: RunFailureCode
    public_message: str
    durable: bool


RunSettlement: TypeAlias = CommittedTurnReceipt | FailedRunReceipt
CancelResult: TypeAlias = Literal["requested", "already_requested", "already_terminal"]


class _RunStateStore(Protocol):
    """Internal protocol for the durable Run state backing store (SQL or in-memory)."""

    async def begin(self, request: RunClaim) -> RunStart: ...

    async def commit(
        self,
        run: ClaimedRun,
        committed: CommittedTurn,
        artifacts: tuple[PromotedArtifact, ...],
        memory_intents: tuple[MemoryPromotionIntent, ...] = (),
    ) -> CommittedTurnReceipt: ...

    async def transition_claim(self, run: ClaimedRun, command: ClaimCommand) -> FailedRunReceipt | None: ...

    async def request_cancel(self, access: TurnAccess, run_id: UUID) -> CancelResult: ...


class _MemoryPromotionOutbox(Protocol):
    """Structural seam for QRE-166 fast-path outcome marking (P23)."""

    async def complete_run(self, run_id: UUID, *, completion_reason: str) -> int: ...

    async def note_run_attempt(self, run_id: UUID, *, reason: str) -> int: ...


class RunLifecycle(Protocol):
    """Public interface for managing Run state transitions across claim, commit, and failure paths.

    Implementations must be safe for concurrent callers: ``heartbeat`` fires from a
    background task while ``finish`` or ``settle`` may run on the execution driver's
    coroutine.  All mutation is expected to be durable (e.g. SQL-backed) so a process
    restart can reconcile settling Runs through ``run_state.reconcile_settling``.
    """

    heartbeat_seconds: int
    stale_after_seconds: int

    async def begin(self, request: RunClaim) -> RunStart: ...

    async def finish(
        self,
        run: ClaimedRun,
        resolution: RLMOutcome | RunFailure,
        *,
        artifact_sink: RunArtifactSink | None = None,
        result_snapshot_sink: ResultSnapshotSink | None = None,
        memory_promotion: OwnedPostCommitMemoryPromotion | None = None,
        memory_intents_builder: MemoryIntentBuilder | None = None,
    ) -> RunSettlement: ...

    async def request_cancel(self, access: TurnAccess, run_id: UUID) -> CancelResult: ...

    async def heartbeat(self, run: ClaimedRun) -> None: ...

    async def settle(self, run: ClaimedRun, failure: RunFailure) -> FailedRunReceipt: ...

    async def revoke_claim(self, run: ClaimedRun, failure: RunFailure) -> FailedRunReceipt: ...

    async def complete_settling(self, run: ClaimedRun) -> FailedRunReceipt: ...


class RunLifecycleService:
    """Coordinate validation, Artifact publication, and atomic Turn state."""

    def __init__(
        self,
        store: _RunStateStore,
        *,
        max_artifact_bytes: int,
        heartbeat_seconds: int = 10,
        stale_after_seconds: int = 60,
        cleanup: RunCleanupSupervisor | None = None,
        memory_outbox: _MemoryPromotionOutbox | None = None,
    ) -> None:
        self._store = store
        self._promotion = ArtifactPromotion(max_bytes=max_artifact_bytes)
        self._max_artifact_bytes = max_artifact_bytes
        self.heartbeat_seconds = heartbeat_seconds
        self.stale_after_seconds = stale_after_seconds
        self._cleanup = cleanup
        self._memory_outbox = memory_outbox

    async def begin(self, request: RunClaim) -> RunStart:
        return await self._store.begin(request)

    async def finish(
        self,
        run: ClaimedRun,
        resolution: RLMOutcome | RunFailure,
        *,
        artifact_sink: RunArtifactSink | None = None,
        result_snapshot_sink: ResultSnapshotSink | None = None,
        memory_promotion: OwnedPostCommitMemoryPromotion | None = None,
        memory_intents_builder: MemoryIntentBuilder | None = None,
    ) -> RunSettlement:
        """
        Finalize a claimed Run by committing a successful Turn or recording a failure.

        Parameters:
            run (ClaimedRun): The claimed Run to finalize.
            resolution (RLMOutcome | RunFailure): The successful outcome or failure result.
            artifact_sink (RunArtifactSink | None): Storage used to read, publish, and clean up artifacts.

        Returns:
            RunSettlement: The receipt for the committed Turn or recorded failure.

        Raises:
            RunLifecycleUnavailableError: If the claim has been revoked.
            RunStateError: If the outcome contains an invalid state.
            RunValidationError: If artifacts are provided without an artifact sink.
            asyncio.CancelledError: If finalization or required cleanup is cancelled.
        """
        if run.authority.revoked:
            raise RunLifecycleUnavailableError("Turn claim is no longer available")
        if isinstance(resolution, RunFailure):
            return await self._transition_receipt(run, FailClaim(_claim_failure(resolution), resolution.usage))
        if not resolution.succeeded:
            await self._rollback(
                artifact_sink,
                (candidate.staging_path for candidate in resolution.artifact_candidates),
            )
            status = resolution.terminal_status
            if status == "completed":
                raise RunStateError("contradictory successful outcome state")
            failure = RunFailure(
                terminal_status=status,
                failure_code=failure_code_for_terminal_status(status),
                public_message=resolution.public_error_message or "Turn failed",
                usage=resolution.usage,
            )
            return await self._transition_receipt(run, FailClaim(_claim_failure(failure), failure.usage))

        candidates = self._promotion.validate(
            resolution.artifact_candidates,
            access=ArtifactAccess(run.access.user_id, run.access.workspace_id),
            session_id=run.session_id,
            run_id=run.run_id,
        )
        if candidates and artifact_sink is None:
            raise RunValidationError("Artifact Candidates require a Run Artifact sink")

        written: list[str] = []
        snapshot_path: str | None = None
        snapshot_task: asyncio.Task[Any] | None = None
        stage = "read_candidates"
        try:
            validated = await self._read_candidates(candidates, artifact_sink)
            stage = "publish_artifacts"
            promoted = await self._publish(candidates, validated, artifact_sink, written)
            if run.authority.revoked:
                raise RunLifecycleUnavailableError("Turn claim is no longer available")
            stage = "build_committed_turn"
            committed = commit_success(resolution, tuple(item.ref for item in promoted))
            if result_snapshot_sink is not None:
                prediction = resolution.prediction
                if prediction is None:
                    raise RunStateError("successful outcome requires a prediction")
                stage = "encode_result_snapshot"
                snapshot_path = result_snapshot_sink.result_path(run.session_id, run.run_id)
                snapshot = encode_result_snapshot(
                    run.session_id,
                    run.run_id,
                    prediction,
                    resolution.usage,
                )
                stage = "write_result_snapshot"
                # Start the snapshot write before the commit so the volume
                # round-trip overlaps with the DB transaction; reconciled after
                # the commit is durable (see _reconcile_snapshot_after_commit).
                snapshot_task = asyncio.ensure_future(result_snapshot_sink.write(snapshot_path, snapshot))
            # P23/QRE-165: pin crash-recoverable Memory promotion intents
            # before the commit so they ride the SAME durable transaction.
            # Build failure degrades softly (optional-side-effect contract).
            memory_intents: tuple[MemoryPromotionIntent, ...] = ()
            raw_candidates = tuple(resolution.memory_candidates) if isinstance(resolution, RLMOutcome) else ()
            if raw_candidates and memory_intents_builder is not None:
                try:
                    memory_intents = memory_intents_builder(run.run_id, raw_candidates)
                except Exception as exc:
                    logger.warning(
                        "Memory promotion intent pinning dropped; Turn commits without intents (%s)",
                        type(exc).__name__,
                        exc_info=exc,
                    )
            stage = "commit_turn"
            if run.authority.revoked:
                raise RunLifecycleUnavailableError("Turn claim is no longer available")
            if memory_intents:
                commit_call = self._store.commit(run, committed, promoted, memory_intents=memory_intents)
            else:
                commit_call = self._store.commit(run, committed, promoted)
            commit_effect = OwnedEffect.start(commit_call)
            try:
                commit_wait = await commit_effect.settle()
                receipt = commit_wait.result()
            except BaseException:
                if commit_effect.caller_cancelled:
                    raise asyncio.CancelledError from None
                raise
        except BaseException as exc:
            if snapshot_task is not None:
                # Never let an in-flight snapshot write race rollback removals.
                with contextlib.suppress(BaseException):
                    await OwnedEffect.from_task(snapshot_task).settle()
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
                run.session_id,
                run.run_id,
            )
            failure = RunFailure(
                "failed",
                "commit_failed",
                "Turn could not be committed",
                resolution.usage,
            )
            return await self._transition_receipt(run, FailClaim(_claim_failure(failure), failure.usage))

        await self._reconcile_snapshot_after_commit(snapshot_task, result_snapshot_sink, snapshot_path)
        await self._promote_memory_candidates_after_commit(resolution, memory_promotion, run_id=run.run_id)
        await self._settle_staging(artifact_sink, candidates)
        return receipt

    async def _promote_memory_candidates_after_commit(
        self,
        resolution: RLMOutcome | RunFailure,
        memory_promotion: OwnedPostCommitMemoryPromotion | None,
        *,
        run_id: UUID | None = None,
    ) -> None:
        """Run one optional, metadata-bounded promotion after the durable commit."""
        candidates = tuple(resolution.memory_candidates) if isinstance(resolution, RLMOutcome) else ()
        if not candidates or memory_promotion is None:
            return
        # P23/QRE-166: best-effort outcome marking on the durable outbox. A
        # marking failure NEVER changes the committed receipt (outbox errors
        # are warnings only) — the reconciler converges any row left pending.
        outbox = self._memory_outbox if run_id is not None else None
        with turn_phase_span(
            "Turn.memory_candidate_promotion",
            inputs={
                "candidate_count": len(candidates),
                "candidate_bytes": sum(int(getattr(item, "byte_size", 0)) for item in candidates),
            },
        ) as span:
            attempt = await memory_promotion.promote(
                candidates,
                timeout_s=_POST_COMMIT_MEMORY_PROMOTION_TIMEOUT_S,
            )
            if attempt.status == "deadline_exceeded":
                await self._mark_run_outbox(run_id, outbox, "note", reason=OUTCOME_DEADLINE_EXCEEDED)
                logger.warning(
                    "Memory Candidate promotion exceeded the bounded post-commit deadline; "
                    "owned cleanup will retain the Run lease"
                )
                span.set_outputs(
                    {
                        "promotion_outcome": "deadline_exceeded",
                        "promoted_count": 0,
                        "deadline_ms": int(_POST_COMMIT_MEMORY_PROMOTION_TIMEOUT_S * 1000),
                    }
                )
                return
            if attempt.status == "interrupted":
                await self._mark_run_outbox(run_id, outbox, "note", reason=OUTCOME_INTERRUPTED)
                logger.warning(
                    "Memory Candidate promotion was interrupted after the Turn commit; owned cleanup retained"
                )
                span.set_outputs({"promotion_outcome": "interrupted", "promoted_count": 0})
                return
            if attempt.status == "failed":
                await self._mark_run_outbox(run_id, outbox, "note", reason=OUTCOME_PROMOTION_FAILED)
                logger.warning("Memory Candidate promotion failed after the Turn commit; Turn remains committed")
                # The Turn is already durable; unlike commit ownership, a post-commit
                # optional side effect preserves the receipt rather than re-raising.
                span.set_outputs({"promotion_outcome": "failed", "promoted_count": 0})
                return
            result = attempt.result
            promoted = int(getattr(result, "promoted_count", 0))
            duplicates = int(getattr(result, "duplicate_count", 0))
            dropped = int(getattr(result, "dropped_count", 0))
            failures = int(getattr(result, "failure_count", 0))
            span.set_outputs(
                {
                    "promotion_outcome": "failed" if failures else "completed",
                    "promoted_count": promoted,
                    "duplicate_count": duplicates,
                    "dropped_count": dropped,
                    "failure_count": failures,
                }
            )
            if failures:
                await self._mark_run_outbox(run_id, outbox, "note", reason=OUTCOME_PROMOTION_FAILED)
                logger.warning(
                    "Memory Candidate promotion dropped %d candidate(s) after the Turn commit; Turn remains committed",
                    failures,
                )
            else:
                await self._mark_run_outbox(run_id, outbox, "complete", reason=OUTCOME_PROMOTED)

    async def _mark_run_outbox(
        self,
        run_id: UUID | None,
        outbox: _MemoryPromotionOutbox | None,
        action: Literal["complete", "note"],
        *,
        reason: str,
    ) -> None:
        if outbox is None or run_id is None:
            return
        try:
            if action == "complete":
                await outbox.complete_run(run_id, completion_reason=reason)
            else:
                await outbox.note_run_attempt(run_id, reason=reason)
        except Exception as exc:
            logger.warning(
                "Memory promotion outbox marking failed for one Run (%s); reconciler converges",
                type(exc).__name__,
                exc_info=exc,
            )

    async def request_cancel(self, access: TurnAccess, run_id: UUID) -> CancelResult:
        return await self._store.request_cancel(access, run_id)

    async def heartbeat(self, run: ClaimedRun) -> None:
        await self._transition(run, HeartbeatClaim())

    async def settle(self, run: ClaimedRun, failure: RunFailure) -> FailedRunReceipt:
        """Revoke commit authority while retaining the durable claim for cleanup."""
        return await self._transition_receipt(run, BeginSettlement(_claim_failure(failure), failure.usage))

    async def revoke_claim(self, run: ClaimedRun, failure: RunFailure) -> FailedRunReceipt:
        """Idempotently classify a Run whose durable claim authority was lost."""
        return await self._transition_receipt(run, RevokeClaim(_claim_failure(failure), failure.usage))

    async def complete_settling(self, run: ClaimedRun) -> FailedRunReceipt:
        """Release a retained claim only after owned cleanup has completed."""
        return await self._transition_receipt(run, CompleteSettlement())

    async def _transition(self, run: ClaimedRun, command: ClaimCommand) -> FailedRunReceipt | None:
        return await self._store.transition_claim(run, command)

    async def _transition_receipt(self, run: ClaimedRun, command: ClaimCommand) -> FailedRunReceipt:
        with turn_phase_span("Turn.claim_transition", inputs={"command": type(command).__name__}):
            receipt = await self._transition(run, command)
        if receipt is None:
            raise RunStateError("claim transition did not return a receipt")
        return receipt

    async def _read_candidates(
        self,
        candidates: tuple[ArtifactCandidate, ...],
        sink: RunArtifactSink | None,
    ) -> tuple[bytes, ...]:
        """
        Read and validate staged artifact candidates.

        Parameters:
                candidates (tuple[ArtifactCandidate, ...]): Artifact candidates to read and validate.
                sink (RunArtifactSink | None): Storage sink containing the staged artifacts.

        Returns:
                tuple[bytes, ...]: Validated artifact contents in candidate order,
                    or an empty tuple when no sink is provided.

        Raises:
                RunIntegrityError: If an artifact's size or checksum does not match its candidate.
                asyncio.CancelledError: If cancellation is requested during reading or validation.
        """
        if sink is None:
            return ()
        # Independent volume reads run concurrently; integrity validation stays
        # sequential and ordered so the first bad candidate still fails fast.
        reads = [
            asyncio.ensure_future(sink.read(candidate.staging_path, max_bytes=self._max_artifact_bytes))
            for candidate in candidates
        ]
        settled = []
        for read in reads:
            effect = OwnedEffect.from_task(read)
            try:
                settled.append((effect, await effect.settle()))
            except BaseException:
                if effect.caller_cancelled:
                    raise asyncio.CancelledError from None
                raise
        values: list[bytes] = []
        cancellation_requested = False
        for candidate, (read_effect, read_wait) in zip(candidates, settled, strict=True):
            cancellation_requested |= read_wait.caller_cancelled
            try:
                data = read_wait.result()
            except BaseException:
                if read_effect.caller_cancelled:
                    raise asyncio.CancelledError from None
                raise
            if len(data) != candidate.byte_size or sha256(data).hexdigest() != candidate.checksum_sha256.lower():
                raise RunIntegrityError("Artifact Candidate bytes failed integrity validation")
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
        """
        Publish validated artifact contents to durable storage.

        Parameters:
                candidates (tuple[ArtifactCandidate, ...]): Artifact metadata and destination paths.
                values (tuple[bytes, ...]): Validated contents corresponding to the candidates.
                sink (RunArtifactSink | None): Storage sink used to write the artifacts.
                written (list[str]): List updated with each destination path before writing.

        Returns:
                tuple[PromotedArtifact, ...]: Promoted artifacts created from the written candidates.
        """
        if sink is None:
            return ()
        artifacts: list[PromotedArtifact] = []
        for candidate, data in zip(candidates, values, strict=True):
            written.append(candidate.durable_path)
            write_effect = OwnedEffect.start(sink.write(candidate.durable_path, data))
            try:
                write_wait = await write_effect.settle()
                write_wait.result()
            except BaseException:
                if write_effect.caller_cancelled:
                    raise asyncio.CancelledError from None
                raise
            if write_wait.caller_cancelled:
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
        try:
            settled = await OwnedEffect.from_task(snapshot_task).settle()
            settled.result()
        except asyncio.CancelledError:
            logger.warning("result snapshot write cancelled after commit; Turn remains committed")
        except Exception:
            logger.warning("result snapshot write failed after commit; Turn remains committed", exc_info=True)
            if sink is not None and snapshot_path is not None:
                await RunLifecycleService._rollback(sink, (snapshot_path,))
        else:
            if settled.caller_cancelled:
                logger.warning("result snapshot write saw cancellation after commit; Turn remains committed")

    async def _settle_staging(
        self,
        sink: RunArtifactSink | None,
        candidates: tuple[ArtifactCandidate, ...],
    ) -> None:
        """Remove staged artifact files using the cleanup supervisor when available.

        Falls back to inline cleanup when the supervisor is unavailable.
        """
        if self._cleanup is not None and sink is not None and candidates:
            staging_paths = tuple(candidate.staging_path for candidate in candidates)

            async def _remove_staging() -> None:
                await self._rollback(sink, staging_paths)

            try:
                self._cleanup.submit(_remove_staging())
                return
            except RunCleanupUnavailableError:
                logger.warning("Turn cleanup capacity unavailable; settling staging inline")
        await self._rollback(sink, (candidate.staging_path for candidate in candidates))

    @staticmethod
    async def _rollback(
        sink: RunArtifactSink | ResultSnapshotSink | None,
        locations,
    ) -> bool:
        """
        Removes supplied artifact or snapshot locations and reports whether cancellation occurred.

        Parameters:
            sink: Sink used to remove the locations.
            locations: Locations to remove.

        Returns:
            `True` if cancellation was requested during removal, `False` otherwise.
        """
        if sink is None:
            return False
        cancellation_requested = False
        for location in locations:
            effect = OwnedEffect.start(sink.remove(location))
            try:
                remove_wait = await effect.settle()
                cancellation_requested |= remove_wait.caller_cancelled
                remove_wait.result()
            except asyncio.CancelledError:
                cancellation_requested = True
            except Exception:
                cancellation_requested |= effect.caller_cancelled
                continue
        return cancellation_requested


__all__ = [
    "CancelResult",
    "ClaimedRun",
    "CommittedRunReplay",
    "CommittedTurnReceipt",
    "FailedRunReceipt",
    "RunAlreadyCompletedError",
    "RunClaim",
    "RunFailure",
    "RunFailureCode",
    "RunIdempotencyMismatchError",
    "RunInProgressError",
    "RunIntegrityError",
    "RunLifecycle",
    "RunLifecycleError",
    "RunLifecycleService",
    "RunLifecycleUnavailableError",
    "RunNotFoundError",
    "RunSettlement",
    "RunStart",
    "RunStateError",
    "RunValidationError",
]
