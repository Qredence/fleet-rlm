"""The streaming Turn route: the one async handler. The Turn itself runs on a dedicated worker pool."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack, suppress
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from fleet_rlm.api import deps
from fleet_rlm.api.deps import LOCKED, Runtime, Settings
from fleet_rlm.api.events import Envelope, SSERuntimeCallback
from fleet_rlm.api.schemas import TurnRequest
from fleet_rlm.api.stream_contracts import frame_envelope
from fleet_rlm.engine.turn import TurnCompletion, run_turn
from fleet_rlm.session import SessionRuntime, update_runtime_preferences

router = APIRouter(tags=["turns"])
_LOGGER = logging.getLogger("fleet_rlm.api")
# The stream loop also releases withheld progress markers while polling for disconnects.
_DISCONNECT_POLL_SECONDS = 0.5
_LOCK_POLL_SECONDS = 0.05
# Turns run here, not on the loop's default executor, so queued Turns cannot starve cancellation,
# lock waits, or lifespan cleanup. Each Session runs at most one Turn, so this bounds concurrent Sessions.
TURN_WORKERS = 16
_OUTCOMES = frozenset({"turn_complete", "error", "turn_cancelled"})


def create_turn_executor() -> ThreadPoolExecutor:
    """The bounded pool that runs Turns; the app lifespan owns and shuts it down."""
    return ThreadPoolExecutor(max_workers=TURN_WORKERS, thread_name_prefix="fleet-turn")


def _turn_executor(request: Request) -> ThreadPoolExecutor:
    """Return the lifespan-owned Turn pool, refusing to run without its lifecycle."""
    executor = getattr(request.app.state, "turn_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="Fleet Turn executor is unavailable")
    return executor


TurnExecutor = Annotated[ThreadPoolExecutor, Depends(_turn_executor)]


async def hold_turn_lock(runtime: Runtime, request: Request) -> AsyncIterator[AsyncExitStack]:  # noqa: ARG001
    """Hold the Session's lock for the whole streamed response, or raise 409 like the other routes.

    A request-scoped dependency: it is released after the stream has finished and the Turn has
    drained, and also when the request fails before the stream starts.
    """
    if not await _acquire(runtime):
        raise HTTPException(status_code=409, detail="A Turn is running on this Session; retry when it completes")
    try:
        async with AsyncExitStack() as cleanup:
            yield cleanup
    finally:
        runtime.turn_lock.release()


TurnLock = Annotated[AsyncExitStack, Depends(hold_turn_lock)]


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
    executor: TurnExecutor,
    _lock: TurnLock,
) -> AsyncIterator[ServerSentEvent]:
    """Run one Turn and stream its activity as Server-Sent Events.

    The stream ends with one outcome event followed by the terminal `done` envelope when `run_turn`
    reached it; a worker that stops earlier still closes the stream with one terminal event.
    A client disconnect cancels an uncommitted Turn. Durable success preserves the warm root
    even before its outcome can be queued. The Session stays locked until sandbox work has drained.
    """
    from fleet_rlm.api.deps import get_registry, translate

    submission = req.model_dump(
        include={"message_id", "file_ids", "regenerates_turn_id", "mode", "model"}, exclude_none=True
    )

    def configure() -> None:
        with translate("Turn configuration is unavailable"):
            prior = (
                runtime.store.turn_by_key(session_id, req.idempotency_key)
                if req.idempotency_key and (req.mode is not None or req.model is not None)
                else None
            )
            if prior is None and (req.mode is not None or req.model is not None):
                update_runtime_preferences(runtime, get_registry(request), session_id, mode=req.mode, model=req.model)

    if req.mode is not None or req.model is not None:
        await anyio.to_thread.run_sync(configure)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
    callback = SSERuntimeCallback(loop, queue, runtime.sandbox_mgr)
    turn_id = uuid.uuid4().hex
    envelope = Envelope(session_id, turn_id, runtime.sandbox_mgr)
    # Executors do not copy contextvars (unlike asyncio.to_thread); DSPy and MLflow keep per-call state
    # in them, so the Turn runs in a copy of the request's context.
    context = contextvars.copy_context()
    completion = TurnCompletion()
    job = executor.submit(
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
        **({"submission": submission} if any(submission.values()) else {}),
        requested_skills=tuple(req.requested_skills),
        timeout_seconds=req.timeout_seconds,
        completion=completion,
    )
    worker = asyncio.wrap_future(job)
    # The wrapper owns a separate exception notification. Always retrieve it, even
    # if it settles after finalization; raw exception text must not reach asyncio logs.
    worker.add_done_callback(lambda completed: None if completed.cancelled() else completed.exception())
    getter: asyncio.Future[dict[str, Any]] | None = None
    connected = True
    finished = False
    reported = False

    @_lock.push_async_callback
    async def drain() -> None:
        """Drain before unlocking, even if SSE forwarding abandons this generator at a yield."""
        if getter is not None:
            getter.cancel()
        callback.close()
        # Shielded: the response may be tearing down, but the lock must outlive the sandbox work.
        with anyio.CancelScope(shield=True):
            if not job.cancel() and not job.done():
                if not finished and not reported:
                    await _cancel(runtime, completion)
                await asyncio.wait({worker})
        _log_failure(job, turn_id)

    try:
        status = envelope.wrap({"event": "turn_status", "phase": "preparation"})
        yield frame_envelope(status)
        while True:
            getter = getter or asyncio.ensure_future(queue.get())
            # Release withheld progress on the loop this stream owns.
            callback.pump_progress()
            done, _ = await asyncio.wait(
                {getter, worker}, timeout=_DISCONNECT_POLL_SECONDS, return_when=asyncio.FIRST_COMPLETED
            )
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
                        await _cancel(runtime, completion)
                continue
            getter = None
            if event is None:
                finished = True
                break
            wrapped = envelope.wrap(event)
            framed = frame_envelope(wrapped)
            # A valid outcome received from the worker remains authoritative even
            # if the peer disconnected before this frame could be sent. Validation
            # must succeed first so malformed outcomes still enter uncommitted
            # teardown in finally.
            reported = reported or event.get("event") in _OUTCOMES
            if connected and await request.is_disconnected():
                connected = False
                if not reported:
                    await _cancel(runtime, completion)
            if connected:
                yield framed
            if event.get("event") == "done":
                # `done` follows teardown and is the Turn's last event; deliver it instead of
                # consuming it, so an HTTP client can tell a clean termination from a dropped stream.
                finished = True
                break
        if connected and not reported and not completion.committed.is_set():
            failure = envelope.wrap(
                {"event": "error", "request_id": turn_id, "error": f"The Turn failed (request {turn_id})."}
            )
            yield frame_envelope(failure)
    finally:
        # The request dependency owns this same stack. Closing it here consumes the finalizer;
        # if forwarding was cancelled instead, dependency teardown runs it before releasing the lock.
        await _lock.aclose()


async def _acquire(runtime: SessionRuntime) -> bool:
    """Try the Session lock for as long as the other routes wait, without blocking the event loop or a thread."""
    deadline = asyncio.get_running_loop().time() + deps._LOCK_WAIT_SECONDS
    while not runtime.turn_lock.acquire(blocking=False):
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(_LOCK_POLL_SECONDS)
    return True


async def _cancel(runtime: SessionRuntime, completion: TurnCompletion) -> None:
    """Record disconnect before waiting for admission or commit to release the lock."""
    completion.disconnected.set()
    with suppress(Exception):
        await asyncio.to_thread(completion.cancel_uncommitted, runtime)


def _log_failure(job: Any, turn_id: str) -> None:
    if job.done() and not job.cancelled() and (exc := job.exception()) is not None:
        _LOGGER.error("Turn %s worker failed outside its outcome (%s)", turn_id, type(exc).__name__)
