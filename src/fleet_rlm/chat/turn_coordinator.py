"""Turn use case: begin, prepare, execute, settle, project, and close."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncGenerator, AsyncIterator
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
    ) -> None:
        self._lifecycle = lifecycle
        self._preparation = preparation
        self._runner = runner
        self._projector = projector or CommittedTurnEventProjector()
        self._turn_timeout_seconds = float(turn_timeout_seconds)
        self._cleanup = cleanup or TurnCleanupSupervisor()

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

        try:
            parameters = inspect.signature(self._preparation.prepare).parameters
            async with asyncio.timeout_at(deadline):
                if "deadline" in parameters:
                    prepared = await self._preparation.prepare(start, deadline=deadline)
                else:  # Compatibility for narrow test/private adapters.
                    prepared = await self._preparation.prepare(start)
        except TurnPreparationCancelled:
            await _shield_cleanup(
                self._lifecycle.finish(
                    start,
                    TurnFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage()),
                )
            )
            raise
        except (TurnPreparationTimeout, TimeoutError) as exc:
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
        return OpenedTurnStream(start.run_id, self._execute(start, prepared))

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
    ) -> AsyncGenerator[RuntimeEvent]:
        stream: TurnEventStream | None = None
        settled = False
        recorder = EventRecorder(turn.run_id, turn.session_id)
        heartbeat_task: asyncio.Task[None] | None = None
        cleanup_handed_off = False
        finalization_task: asyncio.Task[CommittedTurnReceipt | FailedRunReceipt] | None = None
        heartbeat = getattr(self._lifecycle, "heartbeat", None)
        if callable(heartbeat):
            interval = max(1, int(getattr(self._lifecycle, "heartbeat_seconds", 10)))

            async def maintain_claim() -> None:
                while True:
                    await asyncio.sleep(interval)
                    await heartbeat(turn)

            heartbeat_task = asyncio.create_task(maintain_claim())
        try:
            stream = self._runner.stream(prepared.execution)
            last_sequence = 0
            async for event in stream:
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
                self._submit_cleanup(turn, stream, prepared, heartbeat_task)
                heartbeat_task = None
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
                done, _ = await asyncio.wait((finalization_task,), timeout=remaining)
                if finalization_task in done:
                    receipt = finalization_task.result()
                else:
                    receipt = await self._lifecycle.settle(
                        turn,
                        TurnFailure("timeout", "timeout", "Turn timed out", outcome.usage),
                    )
                    cleanup_handed_off = True
                    self._submit_cleanup(turn, stream, prepared, heartbeat_task, finalization_task)
                    heartbeat_task = None
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
                    self._submit_cleanup(turn, stream, prepared, heartbeat_task, finalization_task)
                    heartbeat_task = None
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
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            if not cleanup_handed_off:
                await _shield_cleanup(prepared.aclose())

    def _submit_cleanup(
        self,
        turn: ExecuteTurn,
        stream: TurnEventStream | None,
        prepared: PreparedTurn,
        heartbeat_task: asyncio.Task[None] | None,
        finalization_task: asyncio.Task[CommittedTurnReceipt | FailedRunReceipt] | None = None,
    ) -> None:
        async def cleanup() -> None:
            try:
                if stream is not None:
                    try:
                        await stream.aclose()
                    except BaseException:
                        pass
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
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    await asyncio.gather(heartbeat_task, return_exceptions=True)

        self._cleanup.submit(cleanup())
