"""Turn use case: begin, prepare, execute, settle, project, and close."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Protocol, Self
from uuid import UUID

from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.chat.committed_turn_events import CommittedTurnEventProjector
from fleet_rlm.chat.turn_lifecycle import (
    BeginTurn,
    CommittedTurnReceipt,
    ExecuteTurn,
    FailedRunReceipt,
    ReplayTurn,
    TurnFailure,
    TurnLifecycle,
)
from fleet_rlm.chat.turn_preparation import (
    PreparedTurn,
    TurnPreparation,
    TurnPreparationCancelled,
)
from fleet_rlm.rlm.context import RLMExecutionContext
from fleet_rlm.rlm.events import (
    TERMINAL_DETAIL_TYPES,
    EventRecorder,
    RunBudgetExhausted,
    RunCancelled,
    RunCompleted,
    RunFailed,
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
    if receipt.terminal_status == "budget_exhausted":
        return recorder.record(RunBudgetExhausted())
    message = (
        "Turn could not be committed" if receipt.public_message == "Turn could not be committed" else "Turn failed"
    )
    return recorder.record(RunFailed(code="execution_failed", message=message))


class TurnCoordinator:
    """Own public delivery ordering while domain modules own state and resources."""

    def __init__(
        self,
        *,
        lifecycle: TurnLifecycle,
        preparation: TurnPreparation,
        runner: TurnRunner,
        projector: CommittedTurnEventProjector | None = None,
    ) -> None:
        self._lifecycle = lifecycle
        self._preparation = preparation
        self._runner = runner
        self._projector = projector or CommittedTurnEventProjector()

    async def open(self, command: OpenTurnCommand) -> OpenedTurnStream:
        """Complete claim and preparation before a transport sends headers."""
        request = BeginTurn(
            command.access,
            command.session_id,
            command.input,
            command.idempotency_key,
            command.proposed_run_id,
        )
        start = await self._lifecycle.begin(request)

        if isinstance(start, ReplayTurn):
            return OpenedTurnStream(start.run_id, self._replay(start))

        try:
            prepared = await self._preparation.prepare(start)
        except TurnPreparationCancelled:
            await _shield_cleanup(
                self._lifecycle.finish(
                    start,
                    TurnFailure("cancelled", "Turn cancelled", {}),
                )
            )
            raise
        except Exception:
            await _shield_cleanup(
                self._lifecycle.finish(
                    start,
                    TurnFailure("failed", "Turn could not be prepared", {}),
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
                yield event
            recorder = EventRecorder(turn.run_id, turn.session_id, start_sequence=last_sequence)
            outcome = stream.outcome or RLMOutcome(
                terminal_status="failed",
                public_error_message="Turn failed",
            )
            receipt = await self._lifecycle.finish(
                turn,
                outcome,
                artifact_sink=prepared.artifact_sink,
            )
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
            if stream is not None:
                try:
                    await asyncio.shield(stream.aclose())
                except BaseException:
                    pass
            if not settled:
                try:
                    await asyncio.shield(
                        self._lifecycle.finish(
                            turn,
                            TurnFailure("cancelled", "Turn cancelled", {}),
                            artifact_sink=prepared.artifact_sink,
                        )
                    )
                except Exception:
                    pass
            raise
        except Exception:
            if not settled:
                try:
                    receipt = await asyncio.shield(
                        self._lifecycle.finish(
                            turn,
                            TurnFailure("failed", "Turn failed", {}),
                            artifact_sink=prepared.artifact_sink,
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
            await _shield_cleanup(prepared.aclose())
