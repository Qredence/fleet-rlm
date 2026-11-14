"""Turn activity events: the DSPy callback that feeds the stream, and the wire envelope."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import Future
from typing import Any

import dspy
from dspy.utils.callback import ACTIVE_CALL_ID

from fleet_rlm.observability import LMUsageCallback, activity_preview, activity_text
from fleet_rlm.sandbox.manager import _ACTIVE_MANAGER, DaytonaSandboxManager

_MAX_STATUS_LABEL = 64
_PROGRESS_INTERVAL_SECONDS = 1.0
_TERMINAL_EVENTS = frozenset({"turn_complete", "turn_cancelled", "error", "done"})


def _bounded_scope(value: Any) -> str | None:
    """Return a bounded scope label, or None when it is not a usable string."""
    if not isinstance(value, str):
        return None
    label = value.strip()
    return label[:_MAX_STATUS_LABEL] or None


def _reasoning_text(outputs: Any) -> str:
    """The provider's thinking or reasoning text for the completion, redacted and capped for streaming."""
    message = getattr(outputs, "message", None)
    if message is None and isinstance(outputs, list) and outputs:
        message = getattr(outputs[0], "message", None)
    if message is not None and hasattr(message, "parts_of"):
        with contextlib.suppress(Exception):
            parts = list(message.parts_of(dspy.lm15.ThinkingPart))
            if parts:
                text = "".join(getattr(p, "text", "") for p in parts if getattr(p, "text", None))
                if text:
                    return activity_text(text)
    thinking = getattr(outputs, "thinking", None)
    if isinstance(thinking, str) and thinking:
        return activity_text(thinking)
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
        self._terminal = False
        # One reentrant lock serializes terminal tracking, progress coalescing, call state, and pending futures.
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._pending: set[Future] = set()
        # Bound scheduled loop callbacks too, not just the destination queue.
        self._activity_slots = threading.BoundedSemaphore(queue.maxsize or 100)
        self._calls: dict[str, dict[str, Any]] = {}
        # Progress is the lowest-priority event class: one retained value per scope per second.
        self._progress_pending: dict[tuple[str, str], dict[str, Any]] = {}
        self._progress_last: dict[tuple[str, str], float] = {}

    def close(self) -> None:
        """Stop accepting events and cancel emits still waiting on the consumer."""
        self._closed.set()
        with self._lock:
            for future in self._pending:
                future.cancel()

    def _emit(self, payload: dict[str, Any]) -> None:
        """Offer bounded activity without blocking execution; wait only for terminal delivery.

        Drops activity under backpressure, after close, or after a terminal outcome.
        Outcomes and `done` wait up to 30 seconds for queue space; delivery errors propagate,
        except cancellation caused by closing this callback.
        """
        if self._closed.is_set():
            return
        kind = payload.get("event")
        now = self.clock()
        with self._lock:
            if self._terminal and kind != "done":
                return
            if kind in _TERMINAL_EVENTS:
                self._terminal = True
                self._progress_pending.clear()
                self._progress_last.clear()
            if kind == "progress":
                # Observation only: coalesced, never blocking the producer.
                self._coalesce_progress(payload, now)
                return
        # A withheld progress value is offered before a lifecycle event only when its second has
        # elapsed; it is a non-blocking offer, so a full queue still drops it rather than delaying.
        self._release_progress(now)
        if kind not in _TERMINAL_EVENTS:
            self._deliver_activity(payload)
            return
        future = asyncio.run_coroutine_threadsafe(self.queue.put(payload), self.loop)
        with self._lock:
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
            with self._lock:
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
        with self._lock:
            self._progress_pending[key] = payload
            last = self._progress_last.get(key)
            if last is not None and now - last < _PROGRESS_INTERVAL_SECONDS:
                return
            self._progress_last[key] = now
            newest = self._progress_pending.pop(key)
        self._deliver_activity(newest)

    def _deliver_activity(self, payload: dict[str, Any]) -> None:
        """Offer activity on the loop, dropping it when the bounded handoff or queue is full."""

        def deliver() -> None:
            """Deliver accepted activity before the outcome; discard progress once it is obsolete."""
            try:
                with self._lock:
                    if self._closed.is_set() or (payload.get("event") == "progress" and self._terminal):
                        return
                    with contextlib.suppress(asyncio.QueueFull):
                        self.queue.put_nowait(payload)
            finally:
                self._activity_slots.release()

        with self._lock:
            if self._closed.is_set() or self._terminal or not self._activity_slots.acquire(blocking=False):
                return
            # Serialize acceptance/scheduling with terminal ingestion: accepted activity must be
            # scheduled before terminal queue writes, even when several worker threads emit.
            try:
                self.loop.call_soon_threadsafe(deliver)
            except RuntimeError:
                self._activity_slots.release()
                # The loop closed between the check and the schedule; activity is simply dropped.

    def _take_due_progress(self, now: float) -> list[dict[str, Any]]:
        """Pop the newest withheld value for every scope whose one-second window elapsed."""
        with self._lock:
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
            self._deliver_activity(payload)

    def pump_progress(self) -> None:
        """Deliver the newest withheld progress value for scopes whose second has elapsed.

        Called by the stream loop it already owns; no timer or background task is created.
        """
        with self._lock:
            if self._closed.is_set() or self._terminal:
                return
            for payload in self._take_due_progress(self.clock()):
                try:
                    self.queue.put_nowait(payload)
                except asyncio.QueueFull:
                    break

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
        with self._lock:
            self._calls[call_id] = {"started": time.monotonic(), **identity, **details}
        self._emit({"event": f"{kind}_start", "call_id": call_id, **identity, **details})

    def _end(self, kind: str, call_id: str, exception: BaseException | None, **details: Any) -> None:
        with self._lock:
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
        """Offer activity without waiting; apply backpressure only to outcomes and `done`."""
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
        """Number one event and wrap it in a version-1 envelope with nested payload."""
        self.sequence += 1
        payload = {key: value for key, value in event.items() if key != "event"}
        return {
            "version": 1,
            "event": event.get("event"),
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "agent_id": event.get("agent_id"),
            "parent_agent_id": event.get("parent_agent_id"),
            "sandbox_id": event.get("sandbox_id") or getattr(getattr(self.manager, "_sandbox", None), "id", None),
            "seq": self.sequence,
            "timestamp": time.time(),
            "payload": payload,
        }
