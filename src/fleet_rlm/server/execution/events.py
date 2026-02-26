"""Structured execution-event models and streaming helpers.

This module powers the dedicated ``/ws/execution`` event stream consumed by
Artifact Canvas-style visualizations.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from threading import RLock
from typing import Any, Literal, TYPE_CHECKING

from fastapi import WebSocket
from pydantic import BaseModel

from .sanitizer import sanitize_event_payload, summarize_code_for_event

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .step_builder import ExecutionStepBuilder

ExecutionStepType = Literal["llm", "tool", "repl", "memory", "output"]
ExecutionEventType = Literal[
    "execution_started",
    "execution_step",
    "execution_completed",
]


class ExecutionStep(BaseModel):
    """Single execution graph node/edge payload."""

    id: str
    parent_id: str | None = None
    type: ExecutionStepType
    label: str
    input: Any | None = None
    output: Any | None = None
    timestamp: float


class ExecutionEvent(BaseModel):
    """Top-level event envelope emitted over ``/ws/execution``."""

    type: ExecutionEventType
    run_id: str
    workspace_id: str
    user_id: str
    session_id: str
    step: ExecutionStep | None = None


class ExecutionSubscription(BaseModel):
    """Required identity filter for execution-stream subscriptions."""

    workspace_id: str
    user_id: str
    session_id: str

    def matches(self, event: ExecutionEvent) -> bool:
        return (
            self.workspace_id == event.workspace_id
            and self.user_id == event.user_id
            and self.session_id == event.session_id
        )


class ExecutionEventEmitter:
    """Broadcast ``ExecutionEvent`` payloads to matching websocket subscribers."""

    @dataclass(slots=True)
    class _ConnectionState:
        subscription: ExecutionSubscription
        queue: asyncio.Queue[dict[str, Any] | None]
        sender_task: asyncio.Task[None]
        dropped_events: int = 0

    def __init__(
        self,
        *,
        max_queue: int = 256,
        drop_policy: Literal["drop_oldest", "drop_newest"] = "drop_oldest",
    ) -> None:
        self._max_queue = max(1, int(max_queue))
        self._drop_policy = drop_policy
        self._connections: dict[WebSocket, ExecutionEventEmitter._ConnectionState] = {}
        self._lock = RLock()

    async def connect(
        self, websocket: WebSocket, subscription: ExecutionSubscription
    ) -> None:
        await websocket.accept()
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=self._max_queue
        )
        sender_task = asyncio.create_task(self._sender_loop(websocket))
        state = self._ConnectionState(
            subscription=subscription,
            queue=queue,
            sender_task=sender_task,
        )
        with self._lock:
            self._connections[websocket] = state

    async def disconnect(self, websocket: WebSocket) -> None:
        state: ExecutionEventEmitter._ConnectionState | None = None
        with self._lock:
            state = self._connections.pop(websocket, None)
        if state is None:
            return

        try:
            state.queue.put_nowait(None)
        except asyncio.QueueFull:
            try:
                _ = state.queue.get_nowait()
                state.queue.put_nowait(None)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                logger.debug(
                    "Ignoring execution queue race during disconnect teardown",
                )

        current_task = asyncio.current_task()
        if state.sender_task is not current_task:
            state.sender_task.cancel()
            try:
                await state.sender_task
            except asyncio.CancelledError:
                # Normal outcome: task was cancelled during disconnect teardown.
                pass
            except Exception:
                pass

    async def _sender_loop(self, websocket: WebSocket) -> None:
        state: ExecutionEventEmitter._ConnectionState | None = None
        while True:
            with self._lock:
                state = self._connections.get(websocket)
            if state is None:
                return

            payload = await state.queue.get()
            if payload is None:
                break
            try:
                await websocket.send_json(payload)
            except Exception:
                break

        await self.disconnect(websocket)

    def _enqueue_payload(
        self,
        state: _ConnectionState,
        payload: dict[str, Any],
    ) -> None:
        try:
            state.queue.put_nowait(payload)
            return
        except asyncio.QueueFull:
            pass

        if self._drop_policy == "drop_newest":
            state.dropped_events += 1
            return

        # Default drop policy: keep latest signal and evict the oldest entry.
        try:
            _ = state.queue.get_nowait()
            state.dropped_events += 1
        except asyncio.QueueEmpty:
            state.dropped_events += 1
            return

        try:
            state.queue.put_nowait(payload)
        except asyncio.QueueFull:
            state.dropped_events += 1

    async def emit(self, event: ExecutionEvent) -> None:
        payload = event.model_dump(mode="json")
        with self._lock:
            targets = [
                state
                for state in self._connections.values()
                if state.subscription.matches(event)
            ]
        for state in targets:
            self._enqueue_payload(state, payload)

    def dropped_event_count(self) -> int:
        with self._lock:
            return sum(state.dropped_events for state in self._connections.values())


def __getattr__(name: str) -> Any:
    if name == "ExecutionStepBuilder":
        from .step_builder import ExecutionStepBuilder

        return ExecutionStepBuilder
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ExecutionEvent",
    "ExecutionEventEmitter",
    "ExecutionEventType",
    "ExecutionStep",
    "ExecutionStepBuilder",
    "ExecutionStepType",
    "ExecutionSubscription",
    "sanitize_event_payload",
    "summarize_code_for_event",
]
