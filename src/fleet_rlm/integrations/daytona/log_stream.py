"""Daytona sandbox log streaming with categorized events.

Parses stdout/stderr log lines emitted by the Daytona sandbox during RLM
execution into structured :class:`SandboxEvent` objects, which are relayed to
the frontend via the interpreter's ``_turn_step_callback``.

This replaces the generic "RLM execution in progress (Xs)..." heartbeat with
real, categorized visibility into what the sandbox is doing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

SandboxEventCategory = Literal["code_exec", "tool_call", "output", "error", "status"]


@dataclass(slots=True)
class SandboxEvent:
    """A single categorized event from the Daytona sandbox.

    Attributes
    ----------
    category:
        Event type: ``code_exec``, ``tool_call``, ``output``, ``error``, or
        ``status``.
    message:
        Human-readable event description.
    details:
        Optional structured payload (e.g. ``{"iteration": 3, "tool":
        "llm_query", "duration_ms": 1200}``).
    """

    category: SandboxEventCategory
    message: str
    details: dict[str, Any] | None = None


# Patterns used to categorize raw log lines. Order matters: the first match
# wins, so more specific patterns (tool calls, errors) precede generic ones.
_LOG_PATTERNS: list[tuple[SandboxEventCategory, re.Pattern[str]]] = [
    # Errors and exceptions — highest priority.
    (
        "error",
        re.compile(
            r"\b(error|exception|traceback|failed|failure|raise\s+\w*error)\b",
            re.IGNORECASE,
        ),
    ),
    # Tool calls into the host bridge (llm_query, sub_rlm, fetch_document_text).
    (
        "tool_call",
        re.compile(
            r"\b(llm_query|llm_query_batched|sub_rlm|sub_rlm_batched|"
            r"fetch_document_text|store_evidence|fetch_evidence|list_evidence)\s*\("
        ),
    ),
    # Python code execution markers (REPL prompts, exec/eval calls).
    (
        "code_exec",
        re.compile(r"^(>>>|\.\.\.|exec\(|eval\(|In \[|Out \[)", re.MULTILINE),
    ),
    # Iteration / status milestones emitted by the RLM loop.
    (
        "status",
        re.compile(
            r"\b(iteration|step|starting|finished|complete|progress)\b",
            re.IGNORECASE,
        ),
    ),
]

# Separate pattern used to extract the tool name for tool_call events.
_TOOL_NAME_PATTERN = re.compile(
    r"\b(llm_query|llm_query_batched|sub_rlm|sub_rlm_batched|"
    r"fetch_document_text|store_evidence|fetch_evidence|list_evidence)\s*\("
)


def _categorize_line(line: str) -> SandboxEventCategory:
    """Return the best-matching category for a raw log line.

    Falls back to ``output`` when no specific pattern matches — sandbox logs
    that are plain print() output should still be visible.
    """
    stripped = line.strip()
    if not stripped:
        return "output"
    for category, pattern in _LOG_PATTERNS:
        if pattern.search(stripped):
            return category
    return "output"


def parse_log_line(line: str) -> SandboxEvent | None:
    """Parse a single Daytona sandbox log line into a :class:`SandboxEvent`.

    Returns ``None`` for empty/whitespace-only lines so callers can skip them
    when relaying to the frontend.
    """
    stripped = line.rstrip("\n")
    if not stripped.strip():
        return None
    category = _categorize_line(stripped)
    details: dict[str, Any] | None = None
    # Surface the matched tool name for tool_call events.
    if category == "tool_call":
        match = _TOOL_NAME_PATTERN.search(stripped)
        if match:
            details = {"tool": match.group(1)}
    return SandboxEvent(category=category, message=stripped, details=details)


class LogStreamParser:
    """Subscribe to Daytona sandbox logs and emit categorized events.

    The parser is transport-agnostic: callers feed it log lines (from the
    Daytona SDK log stream, a polled file, or a websocket) via
    :meth:`feed_line`, and it routes parsed :class:`SandboxEvent` objects to
    the configured callback (typically the interpreter's
    ``_turn_step_callback``).
    """

    def __init__(
        self,
        *,
        interpreter: Any | None = None,
        callback: Any | None = None,
        max_events: int = 500,
    ) -> None:
        import threading

        self._interpreter = interpreter
        # Prefer an explicit callback; fall back to the interpreter's step callback.
        self._callback = callback
        self._max_events = max_events
        self._events: list[SandboxEvent] = []
        self._lock = threading.Lock()
        self._started = False

    @property
    def events(self) -> list[SandboxEvent]:
        """Return a copy of the events collected so far."""
        return list(self._events)

    def _emit(self, event: SandboxEvent) -> None:
        """Append to the internal buffer and relay to the callback."""
        with self._lock:
            self._events.append(event)
            if len(self._events) > self._max_events:
                # Drop oldest entries to bound memory.
                self._events = self._events[-self._max_events :]
        callback = self._callback
        if callback is None and self._interpreter is not None:
            callback = getattr(self._interpreter, "_turn_step_callback", None)
        if not callable(callback):
            return
        payload = {
            "phase": f"sandbox_{event.category}",
            "text": event.message,
            "category": event.category,
            "details": event.details,
        }
        try:
            callback(payload)
        except Exception:
            logger.debug("Sandbox event relay failed.", exc_info=True)

    def feed_line(self, line: str) -> SandboxEvent | None:
        """Parse and emit a single log line. Returns the parsed event or None."""
        event = parse_log_line(line)
        if event is not None:
            self._emit(event)
        return event

    def feed_lines(self, lines: list[str]) -> list[SandboxEvent]:
        """Parse and emit a batch of log lines."""
        emitted: list[SandboxEvent] = []
        for line in lines:
            event = self.feed_line(line)
            if event is not None:
                emitted.append(event)
        return emitted

    def start(self) -> None:
        """Mark the parser as started (no-op for transport; callers manage I/O)."""
        self._started = True
        logger.debug("LogStreamParser started.")

    def stop(self) -> None:
        """Mark the parser as stopped and clear the callback reference."""
        self._started = False
        logger.debug("LogStreamParser stopped (%s events collected).", len(self._events))

    def drain(self) -> list[SandboxEvent]:
        """Return all collected events and clear the buffer."""
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events


__all__ = [
    "LogStreamParser",
    "SandboxEvent",
    "SandboxEventCategory",
    "parse_log_line",
]
