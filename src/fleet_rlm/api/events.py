"""Turn activity events: the DSPy callback that feeds the stream, and the wire envelope."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import Future
from typing import Any

from dspy.utils.callback import ACTIVE_CALL_ID

from fleet_rlm.lm_usage import LMUsageCallback
from fleet_rlm.observability import activity_preview, activity_text
from fleet_rlm.sandbox.manager import _ACTIVE_MANAGER, DaytonaSandboxManager


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
    ):
        """Bind the callback to the SSE stream's event loop and queue."""
        super().__init__()
        self.loop = loop
        self.queue = queue
        self.manager = manager
        self._closed = threading.Event()
        self._pending: set[Future] = set()
        self._pending_lock = threading.Lock()
        self._calls: dict[str, dict[str, Any]] = {}
        self._calls_lock = threading.Lock()

    def close(self) -> None:
        """Stop accepting events and cancel emits still waiting on the consumer."""
        self._closed.set()
        with self._pending_lock:
            for future in self._pending:
                future.cancel()

    def _emit(self, payload: dict[str, Any]) -> None:
        # Backpressure reaches the producer rather than discarding a prior outcome.
        if self._closed.is_set():
            return
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
