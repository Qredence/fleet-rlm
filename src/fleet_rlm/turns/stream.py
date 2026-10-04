"""Turn observation stream and terminal event projection."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Self
from uuid import UUID

from fleet_rlm.rlm.events import (
    PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE,
    EventRecorder,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunFailedMessage,
    RunTimedOut,
    RuntimeEvent,
)
from fleet_rlm.rlm.result import RLMOutcome
from fleet_rlm.sessions.run_state import (
    CommittedTurnReceipt,
    FailedRunReceipt,
)
from fleet_rlm.turns.models import shield_cleanup
from fleet_rlm.turns.preparation import PreparedTurn


def terminal(
    recorder: EventRecorder,
    receipt: CommittedTurnReceipt | FailedRunReceipt,
    *,
    trace_id: str | None = None,
) -> RuntimeEvent:
    """Project one durable settlement into the live terminal event."""
    if isinstance(receipt, CommittedTurnReceipt):
        return recorder.record(
            RunCompleted(
                checkpoint_version=receipt.checkpoint_version,
                delivery="live",
                trace_id=trace_id,
            )
        )
    if receipt.terminal_status == "cancelled":
        return recorder.record(RunCancelled())
    if receipt.terminal_status == "timeout":
        return recorder.record(RunTimedOut())
    if receipt.failure_code == "preparation_failed":
        return recorder.record(RunFailed(code="preparation_failed", message="Turn could not be prepared"))
    if receipt.failure_code == "commit_failed":
        return recorder.record(RunFailed(code="commit_failed", message="Turn could not be committed"))
    message = receipt.public_message.strip() if receipt.public_message else ""
    public_message: RunFailedMessage
    if message == "Turn output is too large":
        public_message = "Turn output is too large"
    elif message == "Turn output is invalid":
        public_message = "Turn output is invalid"
    elif message == PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE:
        public_message = PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE
    else:
        public_message = "Turn failed"
    return recorder.record(RunFailed(code="execution_failed", message=public_message))


class OpenedTurnStream:
    """TurnRuntime-owned stream handle with cancellation-resistant close."""

    def __init__(
        self,
        run_id: UUID | None,
        events: AsyncIterator[RuntimeEvent] | None = None,
        *,
        prepared: PreparedTurn | None = None,
        open_task: asyncio.Task[OpenedTurnStream] | None = None,
    ) -> None:
        self.run_id = run_id
        self._events = events
        self._prepared = prepared
        self._open_task = open_task
        self._opened_owner: OpenedTurnStream | None = None
        self._iterator: AsyncIterator[RuntimeEvent] | None = events
        self._opened_resource: Any | None = None
        self._iter_started = False
        self._close_task: asyncio.Task[None] | None = None
        self._close_lock = asyncio.Lock()
        self._close_complete = False
        self._open_error: BaseException | None = None

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> RuntimeEvent:
        await self._resolve_open()
        if self._opened_owner is not None:
            return await self._opened_owner.__anext__()
        if self._iterator is None:
            raise StopAsyncIteration
        self._iter_started = True
        return await self._iterator.__anext__()

    async def wait_open(self, *, timeout: float | None = None) -> OpenedTurnStream | None:
        """Wait for TurnRuntime preparation without cancelling its owned task."""
        if self._events is not None:
            return self
        if self._open_task is None and self._opened_owner is None:
            return self
        try:
            if timeout is None:
                await self._resolve_open()
            else:
                async with asyncio.timeout(max(0.0, timeout)):
                    await self._resolve_open()
        except TimeoutError:
            return None
        return self

    async def _resolve_open(self) -> None:
        if self._opened_owner is not None:
            await self._opened_owner.wait_open()
            return
        if self._events is not None or self._open_task is None:
            return
        try:
            opened = await asyncio.shield(self._open_task)
        except BaseException as exc:
            self._open_error = exc
            raise
        if isinstance(opened, OpenedTurnStream):
            self.run_id = opened.run_id
            self._opened_owner = opened
            await opened.wait_open()
            self.run_id = opened.run_id
            return
        self.run_id = getattr(opened, "run_id", None)
        self._opened_resource = opened
        self._events = opened
        self._iterator = opened.__aiter__()

    async def _close_owned(self) -> None:
        try:
            await self._resolve_open()
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                raise
            if self._events is None:
                return
            raise
        except BaseException:
            # The caller's open path owns the original failure. There are no
            # prepared resources to close when opening never produced a stream.
            if self._events is None:
                return
            raise
        if self._opened_owner is not None:
            await self._opened_owner.aclose()
            return
        if self._iterator is None:
            return
        close_error: BaseException | None = None

        def remember(exc: BaseException) -> None:
            nonlocal close_error
            if close_error is None:
                close_error = exc

        if not self._iter_started:
            self._iter_started = True
            try:
                # Prime and close the Turn generator in the same Context. Its
                # tracing scope may span multiple yields and must reset the
                # ContextVar tokens in the Context where they were created.
                await self._iterator.__anext__()
            except StopAsyncIteration:
                pass
            except BaseException as exc:
                remember(exc)
        close = getattr(self._iterator, "aclose", None)
        if close is not None:
            try:
                await close()
            except BaseException as exc:
                remember(exc)
        opened_close = getattr(self._opened_resource, "aclose", None)
        if opened_close is not None and self._opened_resource is not self._iterator:
            try:
                await shield_cleanup(opened_close())
            except BaseException as exc:
                remember(exc)
        if close_error is not None:
            raise close_error

    async def aclose(self) -> None:
        """Close once and shield the complete TurnRuntime settlement."""
        async with self._close_lock:
            if self._close_complete:
                return
            if self._close_task is None:
                current_task = asyncio.current_task()
                get_context = getattr(current_task, "get_context", None)
                context = get_context() if callable(get_context) else None
                if context is None:
                    current_task = asyncio.current_task()
                    caller_cancelled = False
                    while True:
                        try:
                            await self._close_owned()
                            break
                        except asyncio.CancelledError:
                            if current_task is None or not current_task.cancelling():
                                raise
                            caller_cancelled = True
                            current_task.uncancel()
                    self._close_complete = True
                    if caller_cancelled:
                        raise asyncio.CancelledError
                    return
                self._close_task = asyncio.create_task(
                    self._close_owned(),
                    name="fleet-turn-stream-close",
                    context=context,
                )
            await shield_cleanup(self._close_task)
            self._close_complete = True

    @property
    def outcome(self) -> RLMOutcome | None:
        if self._opened_owner is not None:
            return self._opened_owner.outcome
        return getattr(self._events, "outcome", None)

    async def wait_owned(self) -> None:
        await self._resolve_open()
        if self._opened_owner is not None:
            await self._opened_owner.wait_owned()


__all__ = [
    "OpenedTurnStream",
    "terminal",
]
