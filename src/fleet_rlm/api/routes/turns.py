"""The streaming Turn route: the one async handler. The Turn itself runs on a dedicated worker pool."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from fleet_rlm.api import deps
from fleet_rlm.api.deps import LOCKED, Runtime, Settings
from fleet_rlm.api.events import Envelope, SSERuntimeCallback
from fleet_rlm.api.schemas import TurnRequest
from fleet_rlm.engine.turn import run_turn
from fleet_rlm.session import SessionRuntime

router = APIRouter(tags=["turns"])
_LOGGER = logging.getLogger("fleet_rlm.api")
# The stream loop also samples the advisory status and releases withheld progress markers, so a
# stalled expectation becomes visible without a timer or a background task of its own.
_DISCONNECT_POLL_SECONDS = 0.5
_LOCK_POLL_SECONDS = 0.05
# Turns run here, not on the loop's default executor, so queued Turns cannot starve cancellation,
# lock waits, or lifespan cleanup. Each Session runs at most one Turn, so this bounds concurrent Sessions.
TURN_WORKERS = 16
_OUTCOMES = frozenset({"turn_complete", "error", "turn_cancelled"})


def create_turn_executor() -> ThreadPoolExecutor:
    """The bounded pool that runs Turns; the app lifespan owns and shuts it down."""
    return ThreadPoolExecutor(max_workers=TURN_WORKERS, thread_name_prefix="fleet-turn")


_fallback_executor: ThreadPoolExecutor | None = None
_fallback_lock = threading.Lock()


def _turn_executor(request: Request) -> ThreadPoolExecutor:
    """The lifespan's Turn pool; an app served without its lifespan gets one shared bounded pool."""
    global _fallback_executor
    executor = getattr(request.app.state, "turn_executor", None)
    if executor is not None:
        return executor
    with _fallback_lock:
        if _fallback_executor is None:
            _fallback_executor = create_turn_executor()
        return _fallback_executor


async def hold_turn_lock(runtime: Runtime, request: Request) -> AsyncIterator[None]:
    """Hold the Session's lock for the whole streamed response, or raise 409 like the other routes.

    A request-scoped dependency: it is released after the stream has finished and the Turn has
    drained, and also when the request fails before the stream starts.
    """
    if getattr(request.app.state, "setup_applying", False):
        raise HTTPException(status_code=503, detail="Fleet is restarting after setup")
    if not await _acquire(runtime):
        raise HTTPException(status_code=409, detail="A Turn is running on this Session; retry when it completes")
    try:
        if getattr(request.app.state, "setup_applying", False):
            raise HTTPException(status_code=503, detail="Fleet is restarting after setup")
        yield
    finally:
        runtime.turn_lock.release()


TurnLock = Annotated[None, Depends(hold_turn_lock)]


@router.post(
    "/sessions/{session_id}/turn",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "Server-Sent Events for Turn status, activity, completion, or failure.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        **LOCKED,
    },
)
async def stream_turn(
    session_id: str,
    req: TurnRequest,
    request: Request,
    runtime: Runtime,
    settings: Settings,
    _lock: TurnLock,
) -> AsyncIterator[ServerSentEvent]:
    """Run one Turn and stream its activity as Server-Sent Events.

    The stream ends with one outcome event followed by the terminal `done` envelope when `run_turn`
    reached it; a worker that stops earlier still closes the stream with one terminal event.
    A client disconnect cancels a Turn without an observed outcome; after an outcome it preserves
    the completed Turn's warm root. The Session stays locked until sandbox work has drained.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
    callback = SSERuntimeCallback(loop, queue, runtime.sandbox_mgr)
    turn_id = uuid.uuid4().hex
    envelope = Envelope(session_id, turn_id, runtime.sandbox_mgr)
    # Executors do not copy contextvars (unlike asyncio.to_thread); DSPy and MLflow keep per-call state
    # in them, so the Turn runs in a copy of the request's context.
    context = contextvars.copy_context()
    job = _turn_executor(request).submit(
        context.run,
        run_turn,
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
        timeout_seconds=req.timeout_seconds,
    )
    worker = asyncio.wrap_future(job)
    getter: asyncio.Future[dict[str, Any]] | None = None
    connected = True
    finished = False
    reported = False
    try:
        status = envelope.wrap({"event": "turn_status", "phase": "preparation"})
        yield ServerSentEvent(data=status, id=str(status["seq"]))
        while True:
            getter = getter or asyncio.ensure_future(queue.get())
            # Release withheld progress and sample the derived label on the loop this stream owns.
            callback.pump_progress()
            derived = callback.status_event()
            done, _ = await asyncio.wait(
                {getter, worker}, timeout=_DISCONNECT_POLL_SECONDS, return_when=asyncio.FIRST_COMPLETED
            )
            if derived is not None and connected and not getter.done() and worker not in done:
                wrapped_status = envelope.wrap(derived)
                yield ServerSentEvent(data=wrapped_status, id=str(wrapped_status["seq"]))
            if getter.done():
                event: dict[str, Any] | None = getter.result()
            elif worker in done:
                # The worker ended; take whatever it queued, then stop instead of waiting for a "done" that
                # a failure in run_turn's cleanup may never send.
                getter.cancel()
                event = None if queue.empty() else queue.get_nowait()
            else:
                if connected and await request.is_disconnected():
                    connected = False
                    if not reported:
                        await _cancel(runtime)
                continue
            getter = None
            if event is None:
                finished = True
                break
            reported = reported or event.get("event") in _OUTCOMES
            if connected and await request.is_disconnected():
                connected = False
                if not reported:
                    await _cancel(runtime)
            if connected:
                wrapped = envelope.wrap(event)
                yield ServerSentEvent(data=wrapped, id=str(wrapped["seq"]))
            if event.get("event") == "done":
                # `done` follows teardown and is the Turn's last event; deliver it instead of
                # consuming it, so an HTTP client can tell a clean termination from a dropped stream.
                finished = True
                break
        if connected and not reported:
            failure = envelope.wrap(
                {"event": "error", "request_id": turn_id, "error": f"The Turn failed (request {turn_id})."}
            )
            yield ServerSentEvent(data=failure, id=str(failure["seq"]))
    finally:
        if getter is not None:
            getter.cancel()
        callback.close()
        # Shielded: the response may be tearing down, but the lock must outlive the sandbox work.
        with anyio.CancelScope(shield=True):
            if not job.cancel() and not job.done():
                if not finished and not reported:
                    await _cancel(runtime)
                await asyncio.wait({worker})
        _log_failure(job, turn_id)


async def _acquire(runtime: SessionRuntime) -> bool:
    """Try the Session lock for as long as the other routes wait, without blocking the event loop or a thread."""
    deadline = asyncio.get_running_loop().time() + deps._LOCK_WAIT_SECONDS
    while not runtime.turn_lock.acquire(blocking=False):
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(_LOCK_POLL_SECONDS)
    return True


async def _cancel(runtime: SessionRuntime) -> None:
    """Claim the disconnected Turn's one teardown; a racing terminal branch joins it instead."""
    with suppress(Exception):
        await asyncio.to_thread(runtime.sandbox_mgr.request_cancellation)


def _log_failure(job: Any, turn_id: str) -> None:
    if job.done() and not job.cancelled() and (exc := job.exception()) is not None:
        _LOGGER.error("Turn %s worker failed outside its outcome (%s)", turn_id, type(exc).__name__)
