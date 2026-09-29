"""Minimal durable per-Turn Runtime Event capture.

A Turn's Runtime Events are streamed to its client exactly once. When the client
disconnects mid-Turn, everything already delivered is otherwise lost. This
module owns one small durable copy: a single writer thread, one JSONL file per
Run, and no filesystem work on the event loop.

The store is observation only, never execution authority (see
``fleet_rlm.observability``): every public entry point is fail-soft, the writer
never raises into a Turn, and a disabled store costs one no-op object.

One file per Run lives at ``<root>/captures/<session_id>/<run_id>.jsonl``:
line 1 is ``capture_opened``, then one line per Runtime Event carrying the
envelope fields plus ``kind`` and ``detail``, and the last line is
``capture_closed`` with the stop reason, completeness, truncation, and event
count.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from fleet_rlm.rlm.events import RuntimeEvent

logger = logging.getLogger(__name__)

_WRITER_THREAD_NAME = "fleet-capture-writer"
_SCHEMA_VERSION = 1
# Deliberately module constants rather than policy: they bound one capture
# file's cost and cannot be widened by a TOML edit.
_MAX_EVENTS = 50_000
_MAX_CHARS = 20_000
_QUEUE_DEPTH = 4096
_JOIN_TIMEOUT_S = 5.0

# Terminal Runtime Event kinds and the stop reason they settle as. The
# vocabulary stays local to the capture; the Turn's tracing vocabulary is owned
# by ``fleet_rlm.observability.tracing``.
_TERMINAL_STOP_REASONS = {
    "run.completed": "completed",
    "run.failed": "execution_failure",
    "run.cancelled": "explicit_cancellation",
    "run.timed_out": "turn_timeout",
}
_UNKNOWN_STOP_REASON = "unknown"
_MAX_SEGMENT_CHARS = 64
_SECONDS_PER_DAY = 86_400


class EventCapture(Protocol):
    """Synchronous, fail-soft sink for one Turn's Runtime Events."""

    def record(self, event: RuntimeEvent) -> None: ...

    def mark_stop_reason(self, reason: str) -> None: ...

    def finish(self, *, trace_id: str | None = None) -> None: ...


class EventCaptureSource(Protocol):
    """A capture source that opens one :class:`EventCapture` per Run."""

    def open(self, session_id: UUID | str, run_id: UUID | str) -> EventCapture: ...


class NullEventCapture:
    """No-op capture used when capture is disabled or unavailable."""

    __slots__ = ()

    def record(self, event: RuntimeEvent) -> None:
        del event

    def mark_stop_reason(self, reason: str) -> None:
        del reason

    def finish(self, *, trace_id: str | None = None) -> None:
        del trace_id


@dataclass(frozen=True, slots=True)
class _OpenedJob:
    path: Path
    session_id: str
    run_id: str


@dataclass(frozen=True, slots=True)
class _EventJob:
    path: Path
    event: RuntimeEvent


@dataclass(frozen=True, slots=True)
class _ClosedJob:
    path: Path
    stop_reason: str
    complete: bool
    truncated: bool
    event_count: int
    trace_id: str | None


@dataclass(frozen=True, slots=True)
class _Stop:
    """Sentinel that ends the writer loop once the queue has drained."""


_STOP = _Stop()
_Job = _OpenedJob | _EventJob | _ClosedJob
_QueueItem = _Job | _Stop


def _safe_segment(value: object) -> str:
    """Return one path-safe capture segment without traversal characters."""
    text = str(value)
    cleaned = "".join(char for char in text if char.isalnum() or char in "-_.")
    return cleaned.lstrip(".")[:_MAX_SEGMENT_CHARS] or "unknown"


def _sanitize_capture_text(value: str) -> str:
    """Sanitize one captured string with the committed capture policy."""
    from fleet_rlm.rlm.result import sanitize_capture_text

    return sanitize_capture_text(value, max_len=_MAX_CHARS, redact_paths=False)


def _fields(value: Any) -> dict[str, Any]:
    """Read a dataclass's own fields without deep-copying their values.

    ``dataclasses.asdict`` deep-copies, which fails outright on the frozen
    mapping payloads Runtime Events carry (``MappingProxyType`` cannot be
    pickled); reading fields keeps those payloads intact for sanitization.
    """
    return {field.name: getattr(value, field.name, None) for field in fields(value)}


def _sanitize(value: Any) -> Any:
    """Recursively sanitize captured strings, mappings, sequences, and dataclasses."""
    if isinstance(value, str):
        return _sanitize_capture_text(value)
    if isinstance(value, Mapping):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_sanitize(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _sanitize(_fields(value))
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize(str(value))


def _detail_fields(detail: object) -> Any:
    """Return the sanitized public fields of one detail dataclass."""
    if not is_dataclass(detail) or isinstance(detail, type):
        return {}
    return _sanitize(_fields(detail))


def _render(job: _Job) -> dict[str, Any]:
    """Render one queued job into its JSONL record on the writer thread."""
    if isinstance(job, _OpenedJob):
        return {
            "record": "capture_opened",
            "schema_version": _SCHEMA_VERSION,
            "session_id": job.session_id,
            "run_id": job.run_id,
        }
    if isinstance(job, _EventJob):
        event = job.event
        return {
            "schema_version": int(event.schema_version),
            "event_id": str(event.event_id),
            "run_id": str(event.run_id),
            "session_id": str(event.session_id),
            "sequence": int(event.sequence),
            "timestamp": event.timestamp.isoformat(),
            "kind": str(event.kind),
            "detail": _detail_fields(event.detail),
        }
    return {
        "record": "capture_closed",
        "stop_reason": _sanitize_capture_text(job.stop_reason),
        "complete": job.complete,
        "truncated": job.truncated,
        "event_count": job.event_count,
        "trace_id": _sanitize_capture_text(job.trace_id) if job.trace_id else None,
    }


class _RunCapture:
    """One Run's capture: bounds accounting on the loop, writing off it."""

    __slots__ = ("_complete", "_count", "_finished", "_path", "_stop_reason", "_store", "_truncated")

    def __init__(self, store: TurnCaptureStore, path: Path) -> None:
        self._store = store
        self._path = path
        self._count = 0
        self._truncated = False
        self._complete = False
        self._stop_reason: str | None = None
        self._finished = False

    def record(self, event: RuntimeEvent) -> None:
        """Enqueue one Runtime Event for durable writing."""
        try:
            if self._finished or self._truncated:
                return
            if self._count >= _MAX_EVENTS or self._store.queue_is_saturated():
                self._truncated = True
                return
            reason = _TERMINAL_STOP_REASONS.get(event.kind)
            if reason is not None:
                self._complete = True
                if self._stop_reason is None:
                    self._stop_reason = reason
            self._count += 1
            self._store.submit(_EventJob(self._path, event))
        except Exception:
            self._truncated = True

    def mark_stop_reason(self, reason: str) -> None:
        """Record why the Turn stopped before the stream reached its own end."""
        try:
            if self._finished:
                return
            text = str(reason).strip()
            if text:
                self._stop_reason = text
        except Exception:
            return

    def finish(self, *, trace_id: str | None = None) -> None:
        """Close the capture, flushing the trailing record through the writer."""
        try:
            if self._finished:
                return
            self._finished = True
            self._store.submit(
                _ClosedJob(
                    path=self._path,
                    stop_reason=self._stop_reason or _UNKNOWN_STOP_REASON,
                    complete=self._complete,
                    truncated=self._truncated,
                    event_count=self._count,
                    trace_id=trace_id,
                )
            )
        except Exception:
            logger.warning("Turn capture could not be finalized", exc_info=True)


class TurnCaptureStore:
    """Own the capture writer thread, one file per Run, and capture pruning."""

    def __init__(self, *, root: Path, enabled: bool, retention_days: int, max_captures: int) -> None:
        self._root = Path(root)
        self._enabled = bool(enabled)
        self._retention_days = max(0, int(retention_days))
        self._max_captures = max(1, int(max_captures))
        self._queue: queue.SimpleQueue[_QueueItem] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._disabled: set[Path] = set()

    @property
    def captures_root(self) -> Path:
        """Return the directory holding every captured Run."""
        return self._root / "captures"

    def open(self, session_id: UUID | str, run_id: UUID | str) -> EventCapture:
        """Open the capture for one Run, or a no-op capture when unavailable."""
        try:
            if not self._enabled or self._closed:
                return NullEventCapture()
            path = self.captures_root / _safe_segment(session_id) / f"{_safe_segment(run_id)}.jsonl"
            if not self._start_writer():
                return NullEventCapture()
            self.submit(_OpenedJob(path=path, session_id=str(session_id), run_id=str(run_id)))
            return _RunCapture(self, path)
        except Exception:
            logger.warning("Turn capture could not be opened", exc_info=True)
            return NullEventCapture()

    def submit(self, job: _Job) -> None:
        """Queue one job for the writer thread without touching the filesystem."""
        if self._closed:
            return
        self._queue.put_nowait(job)

    def queue_is_saturated(self) -> bool:
        """Whether the writer queue has reached its depth bound."""
        try:
            return self._queue.qsize() >= _QUEUE_DEPTH
        except Exception:
            return False

    def aclose(self) -> None:
        """Drain queued capture lines and join the writer thread briefly."""
        try:
            self._closed = True
            thread = self._thread
            if thread is None:
                return
            self._queue.put_nowait(_STOP)
            thread.join(timeout=_JOIN_TIMEOUT_S)
            if thread.is_alive():
                logger.warning("Turn capture writer did not stop within the shutdown budget")
        except Exception:
            logger.warning("Turn capture store could not be closed", exc_info=True)

    def _start_writer(self) -> bool:
        thread = self._thread
        if thread is not None and thread.is_alive():
            return True
        with self._lock:
            if self._thread is not None:
                # A dead writer means capture can no longer make progress; do not
                # grow the queue behind it.
                return False
            writer = threading.Thread(target=self._run_writer, name=_WRITER_THREAD_NAME, daemon=True)
            writer.start()
            self._thread = writer
            return True

    def _run_writer(self) -> None:
        self._prune()
        while True:
            try:
                job = self._queue.get()
            except Exception:
                return
            if isinstance(job, _Stop):
                return
            self._write(job)

    def _write(self, job: _Job) -> None:
        if job.path in self._disabled:
            return
        try:
            line = json.dumps(_render(job), ensure_ascii=False, default=str)
            job.path.parent.mkdir(parents=True, exist_ok=True)
            with job.path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")
        except OSError:
            self._disabled.add(job.path)
            logger.warning("Turn capture disabled after a write failure", exc_info=True)
        except Exception:
            logger.warning("Turn capture line could not be written", exc_info=True)

    def _prune(self) -> None:
        """Delete captures past retention, then keep only the newest ones."""
        try:
            root = self.captures_root
            if not root.is_dir():
                return
            cutoff = time.time() - self._retention_days * _SECONDS_PER_DAY
            kept: list[tuple[float, Path]] = []
            for path in root.glob("*/*.jsonl"):
                try:
                    modified = path.stat().st_mtime
                except OSError:
                    continue
                if self._retention_days > 0 and modified < cutoff:
                    _remove(path)
                    continue
                kept.append((modified, path))
            kept.sort(key=lambda item: item[0], reverse=True)
            for _modified, path in kept[self._max_captures :]:
                _remove(path)
        except Exception:
            logger.warning("Turn capture pruning failed", exc_info=True)


def _remove(path: Path) -> None:
    with suppress(OSError):
        path.unlink()
