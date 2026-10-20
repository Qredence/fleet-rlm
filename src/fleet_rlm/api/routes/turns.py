"""The streaming Turn route: the one async handler. The Turn itself runs on a dedicated worker pool."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
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
from fleet_rlm.api.events import Envelope, Heartbeat, SSERuntimeCallback
from fleet_rlm.api.schemas import TurnRequest
from fleet_rlm.engine.turn import run_turn, stalled_outcome
from fleet_rlm.sandbox.repl import SandboxActivity
from fleet_rlm.session import SessionRuntime

router = APIRouter(tags=["turns"])
_LOGGER = logging.getLogger("fleet_rlm.api")
_DISCONNECT_POLL_SECONDS = 15
_LOCK_POLL_SECONDS = 0.05
# Turns run here, not on the loop's default executor, so queued Turns cannot starve cancellation,
# lock waits, or lifespan cleanup. Each Session runs at most one Turn, so this bounds concurrent Sessions.
TURN_WORKERS = 16
_OUTCOMES = frozenset({"turn_complete", "error", "turn_cancelled"})
# The stall watchdog reads local state only (the callback clock and the sandbox's activity snapshot).
_STALL_CHECK_SECONDS = 5.0
_clock = time.monotonic  # the heartbeat clock; tests replace it to cross the stall limit without sleeping


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


async def hold_turn_lock(runtime: Runtime) -> AsyncIterator[None]:
    """Hold the Session's lock for the whole streamed response, or raise 409 like the other routes.

    A request-scoped dependency: it is released after the stream has finished and the Turn has
    drained, and also when the request fails before the stream starts.
    """
    if not await _acquire(runtime):
        raise HTTPException(status_code=409, detail="A Turn is running on this Session; retry when it completes")
    try:
        yield
    finally:
        runtime.turn_lock.release()


TurnLock = Annotated[None, Depends(hold_turn_lock)]


@router.post(
    "/sessions/{session_id}/turn",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": (
                "Server-Sent Events for Turn status, activity, completion, or failure. An `error` event with "
                "`code: turn_stalled` means the stall watchdog stopped the Turn: its sandbox was lost, or no "
                "model/tool activity happened for FLEET_TURN_STALL_SECONDS while no code was running."
            ),
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

    The stream always ends with one outcome event.
    A client disconnect cancels the Turn; the Session stays locked until sandbox work has drained.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
    callback = SSERuntimeCallback(loop, queue, runtime.sandbox_mgr, clock=_clock)
    turn_started = _clock()  # monotonic; a liveness verdict observed before this is left over from an earlier Turn
    turn_id = uuid.uuid4().hex
    envelope = Envelope(session_id, turn_id, runtime.sandbox_mgr)
    watchdog_fired = threading.Event()  # set before the watchdog's cancel, so the worker stores turn_stalled
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
        watchdog_fired=watchdog_fired,
    )
    worker = asyncio.wrap_future(job)
    getter: asyncio.Future[dict[str, Any]] | None = None
    connected = True
    finished = False
    reported = False
    cancel_sent = False
    next_stall_check = loop.time() + _STALL_CHECK_SECONDS
    try:
        status = envelope.wrap({"event": "turn_status", "phase": "preparation"})
        yield ServerSentEvent(data=status, id=str(status["seq"]))
        while True:
            getter = getter or asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait(
                {getter, worker},
                timeout=min(_DISCONNECT_POLL_SECONDS, _STALL_CHECK_SECONDS),
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Events already queued go out first: an outcome queued while the worker is still in its cleanup, or a
            # backlog from a slow reader (which also blocks the worker in emit), is progress, not a stall.
            pending = getter.done() or not queue.empty()
            if connected and not reported and not pending and not worker.done() and loop.time() >= next_stall_check:
                next_stall_check = loop.time() + _STALL_CHECK_SECONDS
                if _is_stalled(
                    callback.heartbeat(),
                    _sandbox_activity(runtime),
                    now=_clock(),
                    limit=settings.turn_stall_seconds,
                    turn_started=turn_started,
                ):
                    _LOGGER.warning("Turn %s stalled; cancelling it", turn_id)
                    watchdog_fired.set()
                    cancel_sent = True
                    await _cancel(runtime)
                    outcome = envelope.wrap(stalled_outcome(turn_id))
                    reported = True
                    yield ServerSentEvent(data=outcome, id=str(outcome["seq"]))
                    break
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
                    cancel_sent = True
                    await _cancel(runtime)
                continue
            getter = None
            if event is None or event.get("event") == "done":
                finished = True
                break
            reported = reported or event.get("event") in _OUTCOMES
            if connected and await request.is_disconnected():
                connected = False
                cancel_sent = True
                await _cancel(runtime)
            if connected:
                wrapped = envelope.wrap(event)
                yield ServerSentEvent(data=wrapped, id=str(wrapped["seq"]))
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
                if not finished and not cancel_sent:
                    await _cancel(runtime)
                await asyncio.wait({worker})
        _log_failure(job, turn_id)


def _sandbox_activity(runtime: SessionRuntime) -> SandboxActivity | None:
    """The Session sandbox's local activity snapshot, or None when it cannot be read (unknown)."""
    probe = getattr(runtime.sandbox_mgr, "sandbox_activity", None)
    if probe is None:
        return None
    try:
        return probe()
    except Exception:
        _LOGGER.debug("Sandbox activity could not be read; treating it as unknown", exc_info=True)
        return None


def _is_stalled(
    beat: Heartbeat, activity: SandboxActivity | None, *, now: float, limit: float, turn_started: float
) -> bool:
    """Whether the Turn has stalled: its sandbox was lost, or it is idle and its callbacks went silent.

    Never before the first `lm_start`, never while a model call or a `spawn_agent`/`spawn_agents` call is
    open (the model runs on the host and children run in their own sandboxes, so the root reports
    nothing), and never without a readable sandbox activity. A root sandbox lost during this Turn stalls
    it even while another tool call is open; otherwise an open tool call (host-side), running sandbox
    code, or host-side sandbox I/O, with or without output, is never a stall. The Turn timeout bounds those.
    """
    if beat.last_event_at is None or beat.open_lm_calls or beat.open_spawn_calls or activity is None:
        return False
    checked = activity.alive_checked_monotonic  # same clock as `now`; None means the verdict's age is unknown
    if activity.alive is False and checked is not None and checked >= turn_started:
        return True
    if beat.open_tool_calls:
        return False
    # busy is model code in the sandbox; host_io_busy is host-side Daytona work (remote exec, file transfers,
    # artifact collection), which the liveness poll does not see.
    working = activity.busy or activity.host_io_busy
    return not working and now - beat.last_event_at >= limit


async def _acquire(runtime: SessionRuntime) -> bool:
    """Try the Session lock for as long as the other routes wait, without blocking the event loop or a thread."""
    deadline = asyncio.get_running_loop().time() + deps._LOCK_WAIT_SECONDS
    while not runtime.turn_lock.acquire(blocking=False):
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(_LOCK_POLL_SECONDS)
    return True


async def _cancel(runtime: SessionRuntime) -> None:
    with suppress(Exception):
        await asyncio.to_thread(runtime.sandbox_mgr.cancel_turn)


def _log_failure(job: Any, turn_id: str) -> None:
    if job.done() and not job.cancelled() and (exc := job.exception()) is not None:
        _LOGGER.error("Turn %s worker failed outside its outcome (%s)", turn_id, type(exc).__name__)
