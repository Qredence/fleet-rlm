"""Turn use case: begin, prepare, execute, settle, project, and close."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, Self
from uuid import UUID

from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.chat.committed_turn_events import CommittedTurnEventProjector
from fleet_rlm.chat.turn_cleanup import TurnCleanupSupervisor, TurnCleanupUnavailable
from fleet_rlm.chat.turn_lifecycle import (
    BeginTurn,
    CommittedTurnReceipt,
    ExecuteTurn,
    FailedRunReceipt,
    ReplayTurn,
    TurnFailure,
    TurnLifecycle,
    TurnLifecycleUnavailable,
    TurnStateError,
)
from fleet_rlm.chat.turn_preparation import (
    PreparedTurn,
    TurnPreparation,
    TurnPreparationCancelled,
    TurnPreparationTimeout,
)
from fleet_rlm.rlm.context import RLMExecutionContext
from fleet_rlm.rlm.dspy_contract import empty_rlm_usage
from fleet_rlm.rlm.events import (
    TERMINAL_DETAIL_TYPES,
    EventRecorder,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunFailedMessage,
    RunStarted,
    RunTimedOut,
    RuntimeEvent,
    Status,
)
from fleet_rlm.rlm.outcome import RLMOutcome


class TurnEventStream(AsyncIterator[RuntimeEvent], Protocol):
    @property
    def outcome(self) -> RLMOutcome | None: ...

    async def aclose(self) -> None: ...


class TurnRunner(Protocol):
    def stream(self, context: RLMExecutionContext) -> TurnEventStream: ...


@dataclass(slots=True)
class _ClaimHeartbeat:
    task: asyncio.Task[None]
    lost: asyncio.Event


async def _shield_cleanup(awaitable) -> None:
    """Complete owned settlement/cleanup even if the caller is cancelled."""
    task = asyncio.ensure_future(awaitable)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.shield(task)
        raise


class OpenedTurnStream:
    """Prepared stream handle whose close shields settlement and cleanup."""

    def __init__(self, run_id: UUID, events: AsyncIterator[RuntimeEvent]) -> None:
        self.run_id = run_id
        self._events = events

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> RuntimeEvent:
        return await self._events.__anext__()

    async def aclose(self) -> None:
        close = getattr(self._events, "aclose", None)
        if close is not None:
            await _shield_cleanup(close())


def _terminal(recorder: EventRecorder, receipt: CommittedTurnReceipt | FailedRunReceipt) -> RuntimeEvent:
    if isinstance(receipt, CommittedTurnReceipt):
        return recorder.record(RunCompleted(checkpoint_version=receipt.checkpoint_version, delivery="live"))
    if receipt.terminal_status == "cancelled":
        return recorder.record(RunCancelled())
    if receipt.terminal_status == "timeout":
        return recorder.record(RunTimedOut())
    if receipt.failure_code == "preparation_failed":
        return recorder.record(RunFailed(code="preparation_failed", message="Turn could not be prepared"))
    if receipt.failure_code == "commit_failed":
        return recorder.record(RunFailed(code="commit_failed", message="Turn could not be committed"))
    message = receipt.public_message.strip() if receipt.public_message else ""
    public_message: RunFailedMessage = (
        "Turn output is invalid" if message == "Turn output is invalid" else "Turn failed"
    )
    return recorder.record(RunFailed(code="execution_failed", message=public_message))


class TurnCoordinator:
    """Own public delivery ordering while domain modules own state and resources."""

    def __init__(
        self,
        *,
        lifecycle: TurnLifecycle,
        preparation: TurnPreparation,
        runner: TurnRunner,
        projector: CommittedTurnEventProjector | None = None,
        turn_timeout_seconds: int | float = 1800,
        cleanup: TurnCleanupSupervisor | None = None,
        claim_loss_fence: Callable[[UUID], Awaitable[None]] | None = None,
    ) -> None:
        self._lifecycle = lifecycle
        self._preparation = preparation
        self._runner = runner
        self._projector = projector or CommittedTurnEventProjector()
        self._turn_timeout_seconds = float(turn_timeout_seconds)
        self._cleanup = cleanup or TurnCleanupSupervisor()
        self._claim_loss_fence = claim_loss_fence

    async def open(self, command: OpenTurnCommand) -> OpenedTurnStream:
        """Complete claim and preparation before a transport sends headers."""
        try:
            self._cleanup.require_capacity()
        except TurnCleanupUnavailable as exc:
            raise TurnLifecycleUnavailable("Turn cleanup capacity is unavailable") from exc
        deadline = asyncio.get_running_loop().time() + self._turn_timeout_seconds
        request = BeginTurn(
            command.access,
            command.session_id,
            command.input,
            command.idempotency_key,
            command.proposed_run_id,
        )
        try:
            async with asyncio.timeout_at(deadline):
                start = await self._lifecycle.begin(request)
        except TimeoutError:
            raise TurnPreparationTimeout("Turn preparation timed out") from None

        if isinstance(start, ReplayTurn):
            return OpenedTurnStream(start.run_id, self._replay(start))

        heartbeat = self._start_heartbeat(start)

        try:
            parameters = inspect.signature(self._preparation.prepare).parameters
            if "deadline" in parameters:
                preparation = self._preparation.prepare(start, deadline=deadline)
            else:  # Compatibility for narrow test/private adapters.
                preparation = self._preparation.prepare(start)
            preparation_task = asyncio.create_task(preparation)
            heartbeat_lost = asyncio.create_task(heartbeat.lost.wait()) if heartbeat is not None else None
            waiters = {preparation_task}
            if heartbeat_lost is not None:
                waiters.add(heartbeat_lost)
            async with asyncio.timeout_at(deadline):
                done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if heartbeat_lost is not None and heartbeat_lost in done:
                assert heartbeat is not None
                if not heartbeat.lost.is_set():
                    raise AssertionError("heartbeat loss waiter completed without claim loss")
                preparation_task.cancel()
                await asyncio.gather(preparation_task, return_exceptions=True)
                self._submit_claim_loss_cleanup(start, heartbeat)
                raise TurnLifecycleUnavailable("Turn claim is no longer available")
            prepared = preparation_task.result()
            if heartbeat_lost is not None:
                heartbeat_lost.cancel()
                await asyncio.gather(heartbeat_lost, return_exceptions=True)
        except TurnPreparationCancelled:
            await self._stop_heartbeat(heartbeat)
            await _shield_cleanup(
                self._lifecycle.finish(
                    start,
                    TurnFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage()),
                )
            )
            raise
        except (TurnPreparationTimeout, TimeoutError) as exc:
            await self._stop_heartbeat(heartbeat)
            await _shield_cleanup(
                self._lifecycle.finish(
                    start,
                    TurnFailure("timeout", "timeout", "Turn preparation timed out", empty_rlm_usage()),
                )
            )
            if isinstance(exc, TurnPreparationTimeout):
                raise
            raise TurnPreparationTimeout("Turn preparation timed out") from None
        except Exception:
            if start.authority.revoked:
                raise
            await self._stop_heartbeat(heartbeat)
            await _shield_cleanup(
                self._lifecycle.finish(
                    start,
                    TurnFailure(
                        "failed",
                        "preparation_failed",
                        "Turn could not be prepared",
                        empty_rlm_usage(),
                    ),
                )
            )
            raise
        return OpenedTurnStream(start.run_id, self._execute(start, prepared, heartbeat))

    async def _replay(self, start: ReplayTurn) -> AsyncGenerator[RuntimeEvent]:
        recorder = EventRecorder(start.run_id, start.session_id)
        yield recorder.record(RunStarted(delivery="replay"))
        yield recorder.record(Status("replay", "running", "idempotent replay"))
        for event in self._projector.project(start.committed_turn, recorder, mode="replay"):
            yield event
        yield recorder.record(RunCompleted(checkpoint_version=start.checkpoint_version, delivery="replay"))

    async def _execute(
        self,
        turn: ExecuteTurn,
        prepared: PreparedTurn,
        heartbeat: _ClaimHeartbeat | None,
    ) -> AsyncGenerator[RuntimeEvent]:
        stream: TurnEventStream | None = None
        settled = False
        recorder = EventRecorder(turn.run_id, turn.session_id)
        cleanup_handed_off = False
        finalization_task: asyncio.Task[CommittedTurnReceipt | FailedRunReceipt] | None = None
        try:
            stream = self._runner.stream(prepared.execution)
            last_sequence = 0
            while True:
                next_event = asyncio.ensure_future(anext(stream))
                heartbeat_lost = asyncio.create_task(heartbeat.lost.wait()) if heartbeat is not None else None
                waiters = {next_event}
                if heartbeat_lost is not None:
                    waiters.add(heartbeat_lost)
                done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                if heartbeat_lost is not None and heartbeat_lost in done:
                    assert heartbeat is not None
                    if not heartbeat.lost.is_set():
                        raise AssertionError("heartbeat loss waiter completed without claim loss")
                    next_event.cancel()
                    await asyncio.gather(next_event, return_exceptions=True)
                    cleanup_handed_off = True
                    self._submit_cleanup(
                        turn,
                        stream,
                        prepared,
                        heartbeat,
                        claim_lost=True,
                        claim_loss_usage=empty_rlm_usage(),
                    )
                    heartbeat = None
                    settled = True
                    yield recorder.record(RunFailed(code="unavailable", message="Turn failed"))
                    return
                if heartbeat_lost is not None:
                    heartbeat_lost.cancel()
                    await asyncio.gather(heartbeat_lost, return_exceptions=True)
                try:
                    event = next_event.result()
                except StopAsyncIteration:
                    break
                if isinstance(event.detail, TERMINAL_DETAIL_TYPES):
                    raise RuntimeError("runner emitted a terminal Runtime Event")
                last_sequence = event.sequence
                recorder = EventRecorder(turn.run_id, turn.session_id, start_sequence=last_sequence)
                yield event
            outcome = stream.outcome or RLMOutcome(
                terminal_status="failed",
                public_error_message="Turn failed",
            )
            if outcome.terminal_status in {"timeout", "cancelled"}:
                status = "timeout" if outcome.terminal_status == "timeout" else "cancelled"
                receipt = await self._lifecycle.settle(
                    turn,
                    TurnFailure(
                        status,
                        status,
                        outcome.public_error_message or ("Turn timed out" if status == "timeout" else "Turn cancelled"),
                        outcome.usage,
                    ),
                )
                cleanup_handed_off = True
                self._submit_cleanup(turn, stream, prepared, heartbeat)
                heartbeat = None
                await asyncio.sleep(0)
            else:
                finalization_task = asyncio.create_task(
                    self._lifecycle.finish(
                        turn,
                        outcome,
                        artifact_sink=prepared.artifact_sink,
                        result_snapshot_sink=prepared.result_snapshot_sink,
                    )
                )
                execution_deadline = float(
                    getattr(
                        prepared.execution,
                        "deadline",
                        asyncio.get_running_loop().time() + self._turn_timeout_seconds,
                    )
                )
                remaining = max(0.0, execution_deadline - asyncio.get_running_loop().time())
                heartbeat_lost = asyncio.create_task(heartbeat.lost.wait()) if heartbeat is not None else None
                waiters = {finalization_task}
                if heartbeat_lost is not None:
                    waiters.add(heartbeat_lost)
                done, _ = await asyncio.wait(waiters, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
                if heartbeat_lost is not None and heartbeat_lost in done:
                    assert heartbeat is not None
                    cleanup_handed_off = True
                    self._submit_cleanup(
                        turn,
                        stream,
                        prepared,
                        heartbeat,
                        finalization_task,
                        claim_lost=True,
                        claim_loss_usage=outcome.usage,
                    )
                    heartbeat = None
                    settled = True
                    yield recorder.record(RunFailed(code="unavailable", message="Turn failed"))
                    return
                if finalization_task in done:
                    if heartbeat_lost is not None:
                        heartbeat_lost.cancel()
                        await asyncio.gather(heartbeat_lost, return_exceptions=True)
                    receipt = finalization_task.result()
                else:
                    if heartbeat_lost is not None:
                        heartbeat_lost.cancel()
                        await asyncio.gather(heartbeat_lost, return_exceptions=True)
                    receipt = await self._lifecycle.settle(
                        turn,
                        TurnFailure("timeout", "timeout", "Turn timed out", outcome.usage),
                    )
                    cleanup_handed_off = True
                    self._submit_cleanup(turn, stream, prepared, heartbeat, finalization_task)
                    heartbeat = None
                    await asyncio.sleep(0)
            settled = True
            if isinstance(receipt, CommittedTurnReceipt):
                for event in self._projector.project(
                    receipt.committed_turn,
                    recorder,
                    mode="live_suffix",
                ):
                    yield event
            yield _terminal(recorder, receipt)
        except (GeneratorExit, asyncio.CancelledError):
            if not settled:
                try:
                    await asyncio.shield(
                        self._lifecycle.settle(
                            turn,
                            TurnFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage()),
                        )
                    )
                    cleanup_handed_off = True
                    self._submit_cleanup(turn, stream, prepared, heartbeat, finalization_task)
                    heartbeat = None
                    await asyncio.sleep(0)
                except Exception:
                    pass
            raise
        except Exception:
            if not settled:
                try:
                    receipt = await asyncio.shield(
                        self._lifecycle.finish(
                            turn,
                            TurnFailure("failed", "execution_failed", "Turn failed", empty_rlm_usage()),
                            artifact_sink=prepared.artifact_sink,
                            result_snapshot_sink=prepared.result_snapshot_sink,
                        )
                    )
                    settled = True
                    yield _terminal(recorder, receipt)
                except Exception:
                    yield recorder.record(RunFailed(code="unavailable", message="Turn failed"))
        finally:
            await self._stop_heartbeat(heartbeat)
            if not cleanup_handed_off:
                await _shield_cleanup(prepared.aclose())

    def _submit_cleanup(
        self,
        turn: ExecuteTurn,
        stream: TurnEventStream | None,
        prepared: PreparedTurn,
        heartbeat: _ClaimHeartbeat | None,
        finalization_task: asyncio.Task[CommittedTurnReceipt | FailedRunReceipt] | None = None,
        *,
        claim_lost: bool = False,
        claim_loss_usage=None,
    ) -> None:
        async def cleanup() -> None:
            try:
                if stream is not None:
                    try:
                        await stream.aclose()
                    except BaseException:
                        pass
                    if claim_lost:
                        await self._revoke_claim(turn, claim_loss_usage or empty_rlm_usage())
                    if claim_lost and self._claim_loss_fence is not None:
                        await self._claim_loss_fence(turn.session_id)
                    wait_owned = getattr(stream, "wait_owned", None)
                    if callable(wait_owned):
                        await wait_owned()
                if finalization_task is not None:
                    try:
                        await asyncio.shield(finalization_task)
                    except BaseException:
                        pass
                await prepared.aclose()
                await self._lifecycle.complete_settling(turn)
            finally:
                await self._stop_heartbeat(heartbeat)

        self._cleanup.submit(cleanup())

    def _start_heartbeat(self, turn: ExecuteTurn) -> _ClaimHeartbeat | None:
        renew = getattr(self._lifecycle, "heartbeat", None)
        if not callable(renew):
            return None
        interval = max(0.01, float(getattr(self._lifecycle, "heartbeat_seconds", 10)))
        stale_after = max(interval * 3, float(getattr(self._lifecycle, "stale_after_seconds", 60)))
        lost = asyncio.Event()

        async def maintain_claim() -> None:
            loop = asyncio.get_running_loop()
            last_success = loop.time()
            next_attempt = last_success + interval
            authority_deadline = last_success + stale_after - interval
            while True:
                await asyncio.sleep(max(0.0, next_attempt - loop.time()))
                try:
                    async with asyncio.timeout_at(authority_deadline):
                        await renew(turn)
                except (TurnLifecycleUnavailable, TurnStateError):
                    turn.authority.revoke()
                    lost.set()
                    return
                except Exception:  # transient persistence failure
                    now = loop.time()
                    if now >= authority_deadline:
                        turn.authority.revoke()
                        lost.set()
                        return
                    next_attempt = min(authority_deadline, now + min(interval, 1.0))
                else:
                    last_success = loop.time()
                    authority_deadline = last_success + stale_after - interval
                    next_attempt = last_success + interval

        return _ClaimHeartbeat(asyncio.create_task(maintain_claim(), name="fleet-turn-heartbeat"), lost)

    @staticmethod
    async def _stop_heartbeat(heartbeat: _ClaimHeartbeat | None) -> None:
        if heartbeat is None:
            return
        heartbeat.task.cancel()
        await asyncio.gather(heartbeat.task, return_exceptions=True)

    def _submit_claim_loss_cleanup(self, turn: ExecuteTurn, heartbeat: _ClaimHeartbeat) -> None:
        async def cleanup() -> None:
            try:
                await self._revoke_claim(turn, empty_rlm_usage())
                if self._claim_loss_fence is not None:
                    await self._claim_loss_fence(turn.session_id)
                await self._lifecycle.complete_settling(turn)
            finally:
                await self._stop_heartbeat(heartbeat)

        self._cleanup.submit(cleanup())

    async def _revoke_claim(self, turn: ExecuteTurn, usage) -> FailedRunReceipt:
        revoke = getattr(self._lifecycle, "revoke_claim", None)
        failure = TurnFailure("failed", "stale_claim", "Turn failed", usage)
        if callable(revoke):
            return await revoke(turn, failure)
        return await self._lifecycle.settle(turn, failure)
