"""Structured execution-event models and streaming helpers.

This module powers the dedicated ``/ws/execution`` event stream consumed by
Artifact Canvas-style visualizations.
"""

from __future__ import annotations

import asyncio
import logging
from asyncio import Lock as AsyncLock
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

from fastapi import WebSocket
from pydantic import BaseModel, Field

from .sanitizer import sanitize_event_payload, summarize_code_for_event

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .step_builder import ExecutionStepBuilder

ExecutionStepType = Literal["llm", "tool", "repl", "memory", "output"]
ExecutionActorKind = Literal["root_rlm", "sub_agent", "delegate", "unknown"]
BackendEventKind = Literal[
    "turn_started",
    "status",
    "reasoning",
    "tool_call",
    "tool_result",
    "sandbox_exec",
    "rlm_delegate",
    "warning",
    "clarification",
    "text",
    "turn_completed",
    "turn_failed",
]
ExecutionEventType = Literal[
    "execution_started",
    "execution_step",
    "execution_completed",
]


class RuntimeEventContext(BaseModel):
    """Stable runtime context attached to backend-emitted events."""

    runtime_mode: str | None = None
    execution_mode: str | None = None
    execution_profile: str | None = None
    sandbox_id: str | None = None
    child_sandbox_id: str | None = None
    volume_name: str | None = None
    workspace_path: str | None = None
    repo_url: str | None = None
    repo_ref: str | None = None
    document_path: str | None = None
    depth: int | None = None
    max_depth: int | None = None
    actor_kind: ExecutionActorKind | None = None
    actor_id: str | None = None
    parent_id: str | None = None
    lane_key: str | None = None
    llm_call_budget: int | None = None


class BackendEvent(BaseModel):
    """Canonical backend event before projection to chat or workbench streams."""

    kind: BackendEventKind
    text: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    runtime: RuntimeEventContext | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExecutionStep(BaseModel):
    """Single execution graph node/edge payload."""

    id: str
    parent_id: str | None = None
    type: ExecutionStepType
    label: str
    depth: int | None = None
    actor_kind: ExecutionActorKind | None = None
    actor_id: str | None = None
    lane_key: str | None = None
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
    summary: dict[str, Any] | None = None


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
        self._lock = AsyncLock()

    async def connect(
        self,
        websocket: WebSocket,
        subscription: ExecutionSubscription,
        *,
        accept: bool = True,
    ) -> None:
        if accept:
            await websocket.accept()
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=self._max_queue)
        sender_task = asyncio.create_task(self._sender_loop(websocket))
        state = self._ConnectionState(
            subscription=subscription,
            queue=queue,
            sender_task=sender_task,
        )
        async with self._lock:
            self._connections[websocket] = state

    async def disconnect(self, websocket: WebSocket) -> None:
        state: ExecutionEventEmitter._ConnectionState | None = None
        async with self._lock:
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
                logger.exception(
                    "Unexpected error while awaiting sender_task during disconnect teardown",
                )

    async def _sender_loop(self, websocket: WebSocket) -> None:
        state: ExecutionEventEmitter._ConnectionState | None = None
        while True:
            async with self._lock:
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
        async with self._lock:
            targets = [state for state in self._connections.values() if state.subscription.matches(event)]
        for state in targets:
            self._enqueue_payload(state, payload)

    async def dropped_event_count(self) -> int:
        async with self._lock:
            return sum(state.dropped_events for state in self._connections.values())


# Late import avoids a circular dependency during module initialization while
# still providing a concrete symbol for static analyzers and re-export users.
from .step_builder import ExecutionStepBuilder  # noqa: E402

__all__ = [
    "BackendEvent",
    "BackendEventKind",
    "ExecutionEvent",
    "ExecutionEventEmitter",
    "ExecutionActorKind",
    "ExecutionEventType",
    "ExecutionStep",
    "ExecutionStepBuilder",
    "ExecutionStepType",
    "ExecutionSubscription",
    "RuntimeEventContext",
    "sanitize_event_payload",
    "summarize_code_for_event",
]
