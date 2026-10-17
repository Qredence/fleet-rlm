"""The streaming Turn route: the one async handler. The Turn itself runs on a worker thread."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from fleet_rlm.api.deps import Runtime, Settings
from fleet_rlm.api.events import Envelope, SSERuntimeCallback
from fleet_rlm.api.schemas import TurnRequest
from fleet_rlm.engine.turn import run_turn
from fleet_rlm.session import SessionRuntime

router = APIRouter(tags=["turns"])
_DISCONNECT_POLL_SECONDS = 15


@router.post(
    "/sessions/{session_id}/turn",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "Server-Sent Events for Turn status, activity, completion, or failure.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def stream_turn(
    session_id: str, req: TurnRequest, request: Request, runtime: Runtime, settings: Settings
) -> AsyncIterator[ServerSentEvent]:
    await _acquire(runtime)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
    callback = SSERuntimeCallback(loop, queue, runtime.sandbox_mgr)
    turn_id = uuid.uuid4().hex
    envelope = Envelope(session_id, turn_id, runtime.sandbox_mgr)
    future = loop.run_in_executor(
        None,
        lambda: run_turn(
            runtime,
            settings,
            session_id=session_id,
            turn_id=turn_id,
            task=req.task,
            idempotency_key=req.idempotency_key,
            mlflow_experiment_id=request.app.state.mlflow_experiment_id,
            callback=callback,
            emit=callback.emit,
            record_replay=req.record_replay,
        ),
    )
    try:
        status = envelope.wrap({"event": "turn_status", "phase": "preparation"})
        yield ServerSentEvent(data=status, id=str(status["seq"]))
        connected = True
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_DISCONNECT_POLL_SECONDS)
            except TimeoutError:
                if connected and await request.is_disconnected():
                    connected = False
                    await _cancel(runtime)
                continue
            if event.get("event") == "done":
                break
            if connected and await request.is_disconnected():
                connected = False
                await _cancel(runtime)
            if connected:
                wrapped = envelope.wrap(event)
                yield ServerSentEvent(data=wrapped, id=str(wrapped["seq"]))
        await future
    finally:
        # Keep this Session serialized until sandbox work has drained after a disconnect.
        callback.close()
        if not future.done():
            await _cancel(runtime)
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    continue
        runtime.turn_lock.release()


async def _acquire(runtime: SessionRuntime) -> None:
    """Wait for any running Turn or workspace change on this Session without blocking the event loop.

    If the client goes away while waiting, the lock is released as soon as the worker gets it.
    """
    acquiring = asyncio.ensure_future(asyncio.to_thread(runtime.turn_lock.acquire))
    try:
        await asyncio.shield(acquiring)
    except asyncio.CancelledError:
        acquiring.add_done_callback(lambda _done: runtime.turn_lock.release())
        raise


async def _cancel(runtime: SessionRuntime) -> None:
    with suppress(Exception):
        await asyncio.to_thread(runtime.sandbox_mgr.cancel_turn)
