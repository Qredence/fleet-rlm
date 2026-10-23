"""Turn activity events: the DSPy callback that feeds the stream, and the wire envelope."""

from __future__ import annotations

import asyncio
import contextlib
import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

from dspy.utils.callback import ACTIVE_CALL_ID

from fleet_rlm.lm_usage import LMUsageCallback
from fleet_rlm.observability import activity_preview, activity_text
from fleet_rlm.sandbox.manager import _ACTIVE_MANAGER, DaytonaSandboxManager

# Advisory labels derived from events Fleet already emits. Nothing here can act on a Turn.
WAITING_ON_HOST = "waiting_on_host"
PROGRESS_UNKNOWN = "progress_unknown"
PROGRESSING = "progressing"
STALLED = "stalled"
INTERRUPTED = "interrupted"
FENCED = "fenced"
_MAX_STATUS_LABEL = 64
_PROGRESS_INTERVAL_SECONDS = 1.0
# A cell that ended without a trustworthy result: its state is partial, not a user error.
_INTERRUPTED_OUTCOMES = frozenset({"interrupted", "partial_state", "transport_uncertain"})


def _bounded_scope(value: Any) -> str | None:
    """Return a bounded scope label, or None when it is not a usable string."""
    if not isinstance(value, str):
        return None
    label = value.strip()
    return label[:_MAX_STATUS_LABEL] or None


def _expectation(value: Any) -> float | None:
    """Accept only a finite positive expectation; no implicit default exists."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    expectation = float(value)
    return expectation if math.isfinite(expectation) and expectation > 0 else None


@dataclass
class _ActiveScope:
    expect_seconds: float
    advanced: float
    checkpoint: int | None = None


class AdvisoryStatus:
    """Derive the advisory status label from the event stream, using a caller-supplied clock.

    No scope means `progress_unknown` forever: `stalled` requires a declared expectation, so
    silence alone can never become a watchdog. The tracker only reads events and time; it
    never cancels, pauses, retries, or shortens anything.
    """

    def __init__(self) -> None:
        self._host_calls: set[str] = set()
        self._host_wait_started: float | None = None
        self._scopes: dict[tuple[str, str], _ActiveScope] = {}
        self._terminal = False
        self._special: str | None = None
        self._last_emitted: str | None = None

    @property
    def terminal(self) -> bool:
        """True once the Turn has reached its terminal outcome; labels are absent afterwards."""
        return self._terminal

    def ingest(self, event: dict[str, Any], now: float) -> None:
        """Update derived state from one event; `now` is a monotonic seconds value."""
        kind = event.get("event")
        if kind == "done" or kind in {"turn_complete", "turn_cancelled", "error"}:
            self._terminal = True
            self._host_calls.clear()
            self._host_wait_started = None
            self._scopes.clear()
            self._special = None
            return
        if kind == "fenced":
            self._special = FENCED
            return
        if kind == "repl_output":
            outcome = event.get("outcome")
            if outcome in _INTERRUPTED_OUTCOMES or event.get("partial_state") is True:
                self._special = INTERRUPTED
        if kind == "tool_start":
            call_id = event.get("call_id")
            if isinstance(call_id, str) and call_id:
                if not self._host_calls:
                    self._host_wait_started = now
                self._host_calls.add(call_id)
            self._special = None
        elif kind == "tool_end":
            call_id = event.get("call_id")
            if isinstance(call_id, str) and call_id in self._host_calls:
                self._host_calls.discard(call_id)
                if not self._host_calls and self._host_wait_started is not None:
                    wait_started = self._host_wait_started
                    self._host_wait_started = None
                    for state in self._scopes.values():
                        state.advanced += max(0.0, now - max(wait_started, state.advanced))
            self._special = None
        elif kind == "progress":
            self._ingest_progress(event, now)
            self._special = None
        elif kind == "repl_code":
            self._special = None

    def _ingest_progress(self, event: dict[str, Any], now: float) -> None:
        """Track valid scope starts, advancing checkpoints, and exits using monotonic seconds.

        Malformed observations are ignored; repeated or lower checkpoints do not renew a scope.
        """
        phase = event.get("phase")
        scope = _bounded_scope(event.get("scope"))
        if scope is None:
            return
        key = (str(event.get("agent_id") or "root"), scope)
        if phase == "start":
            expectation = _expectation(event.get("expect_seconds"))
            if expectation is not None:
                self._scopes[key] = _ActiveScope(expectation, now)
        elif phase == "checkpoint":
            state = self._scopes.get(key)
            value = event.get("checkpoint")
            if state is None or isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return
            # Only a value above the high-water mark renews the expectation window.
            if state.checkpoint is None or value > state.checkpoint:
                state.checkpoint = value
                state.advanced = now
        elif phase == "exit":
            self._scopes.pop(key, None)

    def _stalled_scope(self, now: float) -> tuple[str, _ActiveScope] | None:
        """Return the first scope at or past its expectation in monotonic seconds, or None."""
        for (_, scope), state in self._scopes.items():
            if now - state.advanced >= state.expect_seconds:
                return scope, state
        return None

    def label(self, now: float) -> str | None:
        """Return the current advisory label, or None after the Turn acknowledged its outcome."""
        if self._terminal:
            return None
        if self._special is not None:
            return self._special
        if self._host_calls:
            return WAITING_ON_HOST
        if self._stalled_scope(now) is not None:
            return STALLED
        if self._scopes:
            return PROGRESSING
        return PROGRESS_UNKNOWN

    def detail(self, now: float) -> dict[str, Any]:
        """Bounded scalar evidence behind the current label, with no percentage or completion claim."""
        stalled = self._stalled_scope(now)
        if stalled is not None:
            scope, state = stalled
            return {
                "scope": scope,
                "checkpoint": state.checkpoint,
                "expect_seconds": state.expect_seconds,
                "elapsed_since_advance": int(max(0.0, now - state.advanced)),
            }
        if self._scopes:
            (_, scope), state = next(iter(self._scopes.items()))
            return {
                "scope": scope,
                "checkpoint": state.checkpoint,
                "expect_seconds": state.expect_seconds,
                "elapsed_since_advance": int(max(0.0, now - state.advanced)),
            }
        return {}

    def status_event(self, now: float) -> dict[str, Any] | None:
        """Return one status event when the derived label changed since the last one, else None."""
        label = self.label(now)
        if label is None or label == self._last_emitted:
            return None
        self._last_emitted = label
        return {"event": "status", "status": label, **self.detail(now)}

    def annotate(self, payload: dict[str, Any], now: float) -> dict[str, Any]:
        """Attach the derived label to one outgoing event payload."""
        label = self.label(now)
        return payload if label is None else {**payload, "status": label}


def _reasoning_text(outputs: Any) -> str:
    """The provider's reasoning_content for the first completion, redacted and capped for streaming."""
    first = outputs[0] if isinstance(outputs, list) and outputs else None
    reasoning = first.get("reasoning_content") if isinstance(first, dict) else None
    return activity_text(reasoning) if isinstance(reasoning, str) else ""


class SSERuntimeCallback(LMUsageCallback):
    """Send bounded activity metadata to the active SSE stream."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[dict[str, Any]],
        manager: DaytonaSandboxManager | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ):
        """Bind the callback to the SSE stream's event loop and queue."""
        super().__init__()
        self.loop = loop
        self.queue = queue
        self.manager = manager
        self.clock = clock or time.monotonic
        self.status = AdvisoryStatus()
        # Workers ingest events while the SSE loop samples status; both touch the scope map.
        self._status_lock = threading.Lock()
        self._closed = threading.Event()
        self._pending: set[Future] = set()
        self._pending_lock = threading.Lock()
        self._calls: dict[str, dict[str, Any]] = {}
        self._calls_lock = threading.Lock()
        # Progress is the lowest-priority event class: one retained value per scope per second.
        self._progress_lock = threading.Lock()
        self._progress_pending: dict[tuple[str, str], dict[str, Any]] = {}
        self._progress_last: dict[tuple[str, str], float] = {}

    def close(self) -> None:
        """Stop accepting events and cancel emits still waiting on the consumer."""
        self._closed.set()
        with self._pending_lock:
            for future in self._pending:
                future.cancel()

    def _emit(self, payload: dict[str, Any]) -> None:
        # Backpressure reaches the producer rather than discarding a prior outcome.
        """Queue an annotated event from a worker; coalesce progress without blocking.

        Drops events after close and non-`done` events after a terminal outcome. Other
        queue writes wait up to 30 seconds, raising TimeoutError for a slow consumer;
        queue or loop errors propagate, except cancellation caused by closing this callback.
        """
        if self._closed.is_set():
            return
        now = self.clock()
        with self._status_lock:
            if self.status.terminal and payload.get("event") != "done":
                return
            self.status.ingest(payload, now)
            payload = self.status.annotate(payload, now)
            if self.status.terminal:
                with self._progress_lock:
                    self._progress_pending.clear()
                    self._progress_last.clear()
            if payload.get("event") == "progress":
                # Observation only: coalesced, never blocking the producer.
                self._coalesce_progress(payload, now)
                return
        # A withheld progress value is offered before a lifecycle event only when its second has
        # elapsed; it is a non-blocking offer, so a full queue still drops it rather than delaying.
        self._release_progress(now)
        future = asyncio.run_coroutine_threadsafe(self.queue.put(payload), self.loop)
        with self._pending_lock:
            self._pending.add(future)
            if self._closed.is_set():
                future.cancel()
        try:
            future.result(timeout=30)
        except FutureCancelledError:
            if not self._closed.is_set():
                raise
        except TimeoutError:
            future.cancel()
            raise TimeoutError("SSE consumer is too slow") from None
        finally:
            with self._pending_lock:
                self._pending.discard(future)

    def _progress_key(self, payload: dict[str, Any]) -> tuple[str, str] | None:
        """Return the agent and bounded scope key, or None for an unusable scope."""
        scope = _bounded_scope(payload.get("scope"))
        if scope is None:
            return None
        return str(payload.get("agent_id") or "root"), scope

    def _coalesce_progress(self, payload: dict[str, Any], now: float) -> None:
        """Retain at most one progress event per second per scope, keeping the newest value."""
        key = self._progress_key(payload)
        if key is None:
            return
        with self._progress_lock:
            self._progress_pending[key] = payload
            last = self._progress_last.get(key)
            if last is not None and now - last < _PROGRESS_INTERVAL_SECONDS:
                return
            self._progress_last[key] = now
            newest = self._progress_pending.pop(key)
        self._deliver_progress(newest)

    def _deliver_progress(self, payload: dict[str, Any]) -> None:
        """Offer one progress marker without waiting; a full queue drops it, never delays it."""
        loop = self.loop
        queue = self.queue
        if self._closed.is_set():
            return

        def deliver() -> None:
            """Offer progress only while the stream is live, dropping it if the queue is full."""
            with self._status_lock:
                if self._closed.is_set() or self.status.terminal:
                    return
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(payload)

        try:
            loop.call_soon_threadsafe(deliver)
        except RuntimeError:
            # The stream closed between the check and the schedule; the marker is simply dropped.
            return

    def _take_due_progress(self, now: float) -> list[dict[str, Any]]:
        """Pop the newest withheld value for every scope whose one-second window elapsed."""
        with self._progress_lock:
            due = [
                key
                for key in self._progress_pending
                if (last := self._progress_last.get(key)) is None or now - last >= _PROGRESS_INTERVAL_SECONDS
            ]
            deliveries = []
            for key in due:
                self._progress_last[key] = now
                deliveries.append(self._progress_pending.pop(key))
        return deliveries

    def _release_progress(self, now: float) -> None:
        """Offer due progress markers from a worker thread without ever waiting."""
        for payload in self._take_due_progress(now):
            self._deliver_progress(payload)

    def pump_progress(self) -> None:
        """Deliver the newest withheld progress value for scopes whose second has elapsed.

        Called by the stream loop it already owns; no timer or background task is created.
        """
        with self._status_lock:
            if self._closed.is_set() or self.status.terminal:
                return
            for payload in self._take_due_progress(self.clock()):
                try:
                    self.queue.put_nowait(payload)
                except asyncio.QueueFull:
                    break

    def status_event(self) -> dict[str, Any] | None:
        """Return one derived status transition, sampled by the stream loop."""
        with self._status_lock:
            return self.status.status_event(self.clock())

    def _start(self, kind: str, call_id: str, **details: Any) -> None:
        manager = _ACTIVE_MANAGER.get() or self.manager
        identity = (
            {
                "agent_id": manager.agent_id,
                "parent_agent_id": manager.parent.agent_id if manager.parent else None,
                "sandbox_id": getattr(manager._sandbox, "id", None),
            }
            if manager is not None
            else {}
        )
        details["parent_call_id"] = ACTIVE_CALL_ID.get()
        with self._calls_lock:
            self._calls[call_id] = {"started": time.monotonic(), **identity, **details}
        self._emit({"event": f"{kind}_start", "call_id": call_id, **identity, **details})

    def _end(self, kind: str, call_id: str, exception: BaseException | None, **details: Any) -> None:
        with self._calls_lock:
            started = self._calls.pop(call_id, {})
        timestamp = started.pop("started", None)
        started.pop("inputs", None)
        self._emit(
            {
                "event": f"{kind}_end",
                "call_id": call_id,
                **started,
                "duration_ms": round((time.monotonic() - timestamp) * 1000) if timestamp is not None else None,
                "ok": exception is None,
                "error_type": type(exception).__name__ if exception else None,
                **details,
            }
        )

    def on_module_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:  # noqa: ARG002
        """Emit a module-start event with the predictor's class name."""
        self._start("module", call_id, module=instance.__class__.__name__)

    def on_module_end(self, call_id: str, outputs: Any, exception: BaseException | None = None) -> None:  # noqa: ARG002
        """Emit a module-end event with status only, never predictor outputs."""
        # Predictor outputs can contain private reasoning; report status, never harvest them.
        self._end("module", call_id, exception)

    def on_lm_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
        """Emit an LM-start event and record where this call's usage tracking begins."""
        model = str(getattr(instance, "model", "unknown"))
        super().on_lm_start(call_id, instance, inputs)
        self._start("lm", call_id, model=model[:150])

    def on_lm_end(self, call_id: str, outputs: Any, exception: BaseException | None = None) -> None:
        """Emit numeric usage from the same isolated tracker that annotates the LM span."""
        usage = self.finish_usage(call_id)
        # Reasoning is streamed on purpose (operator decision). Module outputs are still never harvested.
        reasoning = _reasoning_text(outputs)
        self._end(
            "lm",
            call_id,
            exception,
            **({"usage": usage} if usage else {}),
            **({"reasoning": reasoning} if reasoning else {}),
        )

    def on_tool_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
        """Emit a tool-start event with the tool name and a bounded input preview."""
        self._start(
            "tool",
            call_id,
            tool=str(getattr(instance, "name", instance.__class__.__name__))[:100],
            source="dspy",
            inputs=activity_preview(inputs),
        )

    def on_tool_end(
        self,
        call_id: str,
        outputs: dict[str, Any] | None,
        exception: BaseException | None = None,
    ) -> None:
        """Emit a tool-end event with a bounded output preview."""
        self._end("tool", call_id, exception, output=activity_preview(outputs))

    def emit(self, payload: dict[str, Any]) -> None:
        """Queue one event for the stream; blocks the producer when the consumer falls behind."""
        self._emit(payload)


class Envelope:
    """Numbered version-1 envelopes for one Turn's events."""

    def __init__(self, session_id: str, turn_id: str, manager: Any) -> None:
        """Store the identifiers and sandbox manager stamped onto each envelope."""
        self.session_id = session_id
        self.turn_id = turn_id
        self.manager = manager
        self.sequence = 0

    def wrap(self, event: dict[str, Any]) -> dict[str, Any]:
        """Number one event and wrap it in a version-1 envelope.

        The payload is nested under `payload` and also spread at the top level.
        """
        self.sequence += 1
        payload = {key: value for key, value in event.items() if key != "event"}
        return {
            "version": 1,
            "event": event.get("event"),
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "agent_id": None,
            "parent_agent_id": None,
            "sandbox_id": getattr(getattr(self.manager, "_sandbox", None), "id", None),
            "seq": self.sequence,
            "timestamp": time.time(),
            "payload": payload,
            **payload,
        }
