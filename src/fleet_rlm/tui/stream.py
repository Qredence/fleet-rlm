"""Turn SSE stream request and event parsing helpers for the TUI."""

from __future__ import annotations

import json
import socket
import ssl
import uuid
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, nullcontext, suppress
from typing import Any
from urllib.parse import quote

import httpx

from fleet_rlm.tui.commands import SessionState
from fleet_rlm.tui.render import TurnTracker


def _parse_sse_line(line: str, state: SessionState) -> tuple[dict[str, Any] | None, bool]:
    """Parse one `data:` SSE line and sync `state`; return `(event, malformed)`."""
    if not line.startswith("data:"):
        return None, False
    try:
        event = json.loads(line[5:].lstrip())
    except json.JSONDecodeError:
        return None, True
    if not isinstance(event, dict) or not isinstance(event.get("payload", {}), dict):
        return None, True
    if event.get("turn_id"):
        state.last_turn_id = str(event["turn_id"])
    payload = {**event, **event.get("payload", {})}
    if payload.get("trace_id"):
        state.last_trace_id = str(payload["trace_id"])
    if payload.get("event") == "triage_decision" and payload.get("mode"):
        state.mode = str(payload["mode"])
    return event, False


def _request_turn(
    client: httpx.Client, session_id: str, task: str, state: SessionState
) -> AbstractContextManager[httpx.Response]:
    """Open the shared Turn HTTP contract; the caller owns response lifetime and cancellation."""
    body: dict[str, Any] = {
        "task": task,
        "idempotency_key": uuid.uuid4().hex,
        "record_replay": state.record_replay,
    }
    if state.requested_skills:
        body["requested_skills"] = list(state.requested_skills)
    # Client-local `/timeout` state travels per Turn. Omitting the field inherits the
    # server configuration; `0` explicitly disables the Fleet deadline for this Turn.
    if state.timeout_seconds is not None:
        body["timeout_seconds"] = state.timeout_seconds
    return client.stream(
        "POST",
        f"/sessions/{quote(session_id, safe='')}/turn",
        json=body,
    )


def _abort_response(response: httpx.Response) -> None:
    """Unblock a synchronous SSE response stream on cancellation without closing its fd mid-recv."""
    network_stream = response.extensions.get("network_stream")
    sock = network_stream.get_extra_info("socket") if hasattr(network_stream, "get_extra_info") else None
    if isinstance(sock, ssl.SSLSocket):
        with suppress(OSError):
            # Bypass SSLSocket.shutdown() so the reader unblocks without racing close() or _sslobj.
            socket.socket.shutdown(sock, socket.SHUT_RDWR)
    elif hasattr(sock, "shutdown"):
        with suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
    else:
        with suppress(Exception):
            response.close()


def _turn_events(
    response: httpx.Response,
    state: SessionState,
    tracker: TurnTracker,
    *,
    lock: AbstractContextManager[Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Generator[dict[str, Any] | None, None, None]:
    """Update shared Turn state from SSE, yielding events or None for malformed data lines."""
    for line in response.iter_lines():
        if cancelled is not None and cancelled():
            break
        with lock if lock is not None else nullcontext():
            event, malformed = _parse_sse_line(line, state)
            if event is not None:
                tracker.ingest(event)
        if malformed:
            yield None
        elif event is not None:
            yield event
