"""Hosted REPL/interpreter event bridge owned by the Agent Framework host."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fleet_rlm.utils.logging import sanitize_for_log as _sanitize_for_log

from .execution_events import (
    HostedExecutionEventRouter,
    HostedExecutionStepBuilder,
    HostedExecutionStepSink,
    normalize_hosted_execution_event,
)

logger = logging.getLogger(__name__)

_REPL_HOOK_STEP_QUEUE_MAX = 128


class ReplHookBridge:
    """Queue and forward hosted interpreter callbacks into execution event flow."""

    def __init__(
        self,
        *,
        ws_loop: asyncio.AbstractEventLoop,
        lifecycle: HostedExecutionStepSink,
        step_builder: HostedExecutionStepBuilder,
        interpreter: Any,
        enqueue_nonblocking: Callable[[asyncio.Queue[Any | None], Any], bool],
        route_event: HostedExecutionEventRouter | None = None,
    ) -> None:
        self._ws_loop = ws_loop
        self._lifecycle = lifecycle
        self._step_builder = step_builder
        self._interpreter = interpreter
        self._enqueue_nonblocking = enqueue_nonblocking
        self._route_event = route_event
        self._previous_execution_hook: Any = None
        self._queue: asyncio.Queue[Any | None] = asyncio.Queue(
            maxsize=_REPL_HOOK_STEP_QUEUE_MAX
        )
        self._worker_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._worker_task = asyncio.create_task(self._repl_step_worker())
        if self._interpreter is None:
            return
        self._previous_execution_hook = getattr(
            self._interpreter, "execution_event_callback", None
        )
        self._interpreter.execution_event_callback = self._dispatch_interpreter_hook

    async def stop(self) -> None:
        if self._interpreter is not None:
            self._interpreter.execution_event_callback = self._previous_execution_hook
        await self._queue.put(None)
        if self._worker_task is not None:
            try:
                await self._worker_task
            except asyncio.CancelledError:
                logger.debug("REPL step worker task was cancelled during shutdown")

    def _dispatch_interpreter_hook(self, payload: dict[str, Any]) -> None:
        previous_hook = self._previous_execution_hook
        if callable(previous_hook):
            try:
                previous_hook(payload)
            except Exception:  # pragma: no cover - defensive callback isolation
                logger.debug("previous_execution_event_callback_failed", exc_info=True)

        normalized_event = normalize_hosted_execution_event(
            payload,
            interpreter=self._interpreter,
        )
        if self._route_event is not None:
            try:
                self._route_event(normalized_event)
            except Exception:  # pragma: no cover - defensive callback isolation
                logger.debug("hosted_execution_event_route_failed", exc_info=True)
        self._interpreter_hook(normalized_event.payload)

    async def _emit_and_persist_repl_step(self, step_data: Any) -> None:
        if self._lifecycle.run_completed:
            return
        try:
            await self._lifecycle.emit_step(step_data)
            await self._lifecycle.persist_step(step_data)
        except Exception as exc:  # pragma: no cover - defensive path
            logger.warning(
                "Failed to emit/persist REPL execution step: %s",
                _sanitize_for_log(exc),
            )
            self._lifecycle.record_persistence_error(exc)

    async def _repl_step_worker(self) -> None:
        while True:
            step_data = await self._queue.get()
            if step_data is None:
                break
            await self._emit_and_persist_repl_step(step_data)

    def _queue_repl_step(self, step_data: Any) -> None:
        if self._lifecycle.run_completed:
            return
        if not self._enqueue_nonblocking(self._queue, step_data):
            logger.debug("Dropped REPL execution step due to queue contention")

    def _interpreter_hook(self, payload: dict[str, Any]) -> None:
        if self._lifecycle.run_completed:
            return
        repl_step = self._step_builder.from_interpreter_hook(payload)
        if repl_step is None:
            return
        self._ws_loop.call_soon_threadsafe(
            lambda step_data=repl_step: self._queue_repl_step(step_data)
        )
