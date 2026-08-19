"""Bounded Runtime Event observation for one owned RLM worker."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any, cast
from uuid import UUID

from fleet_rlm.rlm.context import RLMExecutionContext
from fleet_rlm.rlm.errors import RunCancelledError
from fleet_rlm.rlm.events import (
    EventRecorder,
    RuntimeEvent,
    RuntimeEventDetail,
    SkillActivated,
    SkillLoaded,
    Status,
    StepFinished,
    StepStarted,
    ToolCompleted,
    ToolFailed,
    ToolStarted,
    WarningEvent,
)
from fleet_rlm.rlm.outcome import ExecutionDetail
from fleet_rlm.rlm.worker_execution import RLMWorkerHandle

MAX_DETAIL_EVENTS = 1024
_RETAINED_DETAIL_TYPES = (
    SkillActivated,
    SkillLoaded,
    StepStarted,
    StepFinished,
    ToolStarted,
    ToolCompleted,
    ToolFailed,
)


class DetailRelay:
    """Thread-safe bounded relay retaining lifecycle-critical details."""

    def __init__(self, *, maxsize: int = MAX_DETAIL_EVENTS) -> None:
        """
        Initialize a bounded relay for runtime event details.

        Parameters:
            maxsize (int): Maximum number of ordinary details to retain. Values
                below zero are treated as zero; lifecycle-critical details remain
                retainable.
        """
        self._loop = asyncio.get_running_loop()
        # Step and Tool lifecycle are durable protocol signals, not optional
        # diagnostic detail. Keep them even while normal observation traffic is capped.
        self._queue: asyncio.Queue[RuntimeEventDetail] = asyncio.Queue()
        self._maxsize = max(0, maxsize)
        self._ordinary_count = 0
        self.overflowed = False

    def publish(self, detail: RuntimeEventDetail) -> None:
        """Publish a runtime detail to the relay from the current or another thread."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is self._loop:
            self._put(detail)
        else:
            self._loop.call_soon_threadsafe(self._put, detail)

    def _put(self, detail: RuntimeEventDetail) -> None:
        if self._is_retained(detail):
            self._queue.put_nowait(detail)
            return
        if self._ordinary_count >= self._maxsize:
            self.overflowed = True
            return
        self._ordinary_count += 1
        self._queue.put_nowait(detail)

    @staticmethod
    def _is_retained(detail: RuntimeEventDetail) -> bool:
        """Determine whether a runtime detail is retained regardless of the ordinary event capacity."""
        return isinstance(detail, _RETAINED_DETAIL_TYPES)

    async def get(self) -> RuntimeEventDetail:
        """Retrieve the next retained runtime event detail.

        Returns:
                RuntimeEventDetail: The next available runtime event detail.
        """
        detail = await self._queue.get()
        if not self._is_retained(detail):
            self._ordinary_count -= 1
        return detail

    def drain(self) -> list[RuntimeEventDetail]:
        """
        Remove and return all currently queued runtime event details.

        Returns:
            list[RuntimeEventDetail]: The queued details in queue order.
        """
        values: list[RuntimeEventDetail] = []
        while True:
            try:
                detail = self._queue.get_nowait()
                if not self._is_retained(detail):
                    self._ordinary_count -= 1
                values.append(detail)
            except asyncio.QueueEmpty:
                return values


class WorkerMonitor:
    """Bound polling, cancellation, and deadline policy for one worker."""

    def __init__(
        self,
        worker: RLMWorkerHandle[Any],
        relay: DetailRelay,
        context: RLMExecutionContext,
        drain_capabilities: Callable[[], tuple[ExecutionDetail, ...]],
    ) -> None:
        """Initialize monitoring state for a worker execution.

        Parameters:
                worker: The worker whose execution is monitored.
                relay: The relay that provides runtime details.
                context: The execution context containing cancellation and deadline state.
                drain_capabilities: A callback that retrieves pending capability-generated details.
        """
        self.worker = worker
        self.relay = relay
        self.context = context
        self.drain_capabilities = drain_capabilities
        self.intended_stop: BaseException | None = None
        self.caller_cancelled = False

    async def stream(self) -> AsyncIterator[RuntimeEventDetail]:
        """Stream runtime event details while monitoring worker completion, cancellation, and deadlines.

        Yields:
            RuntimeEventDetail: A detail emitted by the worker or its capabilities.
        """
        pending: asyncio.Task[RuntimeEventDetail] | None = None
        completion = asyncio.create_task(self.worker.wait_until_done(), name="fleet-rlm-worker-completion")
        try:
            while not self.worker.done():
                if await self.context.execution.cancellation_requested():
                    self.intended_stop = RunCancelledError()
                    break
                remaining = self.context.execution.deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    self.intended_stop = TimeoutError()
                    break
                pending = asyncio.create_task(self.relay.get())
                done, _ = await asyncio.wait(
                    {completion, pending},
                    timeout=min(remaining, 0.25),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if pending in done:
                    yield pending.result()
                else:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                pending = None
                for detail in self.drain_capabilities():
                    yield detail
        except (GeneratorExit, asyncio.CancelledError):
            self.caller_cancelled = True
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            if not completion.done():
                completion.cancel()
                await asyncio.gather(completion, return_exceptions=True)
            if self.intended_stop is None and not self.caller_cancelled:
                self.caller_cancelled |= await self.worker.settle_after_caller_cancellation()

    def raise_if_stopped(self) -> None:
        """
        Raise the exception associated with a stopped worker.

        Raises:
            asyncio.CancelledError: If the caller cancelled the observation.
            BaseException: The worker's intended stop exception, when one was recorded.
        """
        if self.caller_cancelled:
            self.worker.consume_exception()
            raise asyncio.CancelledError
        if self.intended_stop is not None:
            self.worker.consume_exception()
            intended_stop = self.intended_stop
            if isinstance(intended_stop, BaseException):
                raise intended_stop


class ObservationSession:
    """Project worker details into recorded Runtime Events and bounded outcome details."""

    def __init__(self, run_id: UUID, session_id: UUID, *, maxsize: int = MAX_DETAIL_EVENTS) -> None:
        """Initialize an observation session for a run and session.

        Parameters:
                run_id (UUID): Identifier of the run associated with the session.
                session_id (UUID): Identifier of the session associated with the run.
                maxsize (int): Maximum number of ordinary execution details retained by the session.
        """
        self._recorder = EventRecorder(run_id, session_id)
        self._relay = DetailRelay(maxsize=maxsize)
        self._details: list[ExecutionDetail] = []

    @property
    def details(self) -> list[ExecutionDetail]:
        """
        Accesses the execution details collected during the observation session.

        Returns:
                list[ExecutionDetail]: The collected execution details.
        """
        return self._details

    @property
    def overflowed(self) -> bool:
        """Indicates whether the relay dropped ordinary details after reaching its capacity.

        Returns:
                bool: `True` if ordinary details were dropped, `False` otherwise.
        """
        return self._relay.overflowed

    def publish(self, detail: RuntimeEventDetail) -> None:
        """Publish an interpreter/tool detail from either the host or worker thread."""
        self._relay.publish(detail)

    def record(self, detail: RuntimeEventDetail) -> RuntimeEvent:
        """
        Record a runtime event and retain non-status details for the execution outcome.

        Parameters:
                detail (RuntimeEventDetail): Runtime detail to record.

        Returns:
                RuntimeEvent: The recorded runtime event.
        """
        if not isinstance(detail, Status):
            self._details.append(cast(ExecutionDetail, detail))
        return self._recorder.record(detail)

    def record_event(self, detail: RuntimeEventDetail) -> RuntimeEvent:
        """Record a stream envelope without treating it as execution detail."""
        return self._recorder.record(detail)

    async def stream_worker(
        self,
        worker: RLMWorkerHandle[Any],
        context: RLMExecutionContext,
        drain_capabilities: Callable[[], tuple[ExecutionDetail, ...]],
    ) -> AsyncIterator[RuntimeEvent]:
        """Yield live worker observations, final drain details, and overflow warning."""
        monitor = WorkerMonitor(worker, self._relay, context, drain_capabilities)
        async for detail in monitor.stream():
            yield self.record(detail)
        for detail in (*drain_capabilities(), *self._relay.drain()):
            yield self.record(detail)
        if self._relay.overflowed:
            yield self.record(WarningEvent("some detailed execution events were omitted"))
        monitor.raise_if_stopped()
