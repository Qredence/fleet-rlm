"""JSON-RPC-like protocol helpers for fleet stdio bridge."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, TextIO

JsonObject = dict[str, Any]


@dataclass(slots=True)
class BridgeRequest:
    """Normalized request payload parsed from one JSON line."""

    request_id: str
    method: str
    params: JsonObject


class BridgeRPCError(RuntimeError):
    """Structured bridge error that maps to a stable error response."""

    def __init__(self, *, code: str, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def parse_request_line(line: str) -> BridgeRequest:
    """Parse one incoming JSON line to a normalized request."""
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise BridgeRPCError(
            code="INVALID_REQUEST",
            message=f"Invalid JSON payload: {exc}",
        ) from exc

    if not isinstance(raw, dict):
        raise BridgeRPCError(
            code="INVALID_REQUEST",
            message="Request payload must be a JSON object.",
        )

    method = str(raw.get("method", "")).strip()
    if not method:
        raise BridgeRPCError(
            code="INVALID_REQUEST",
            message="Request is missing `method`.",
        )

    request_id = raw.get("id")
    request_id_str = "" if request_id is None else str(request_id)

    params = raw.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise BridgeRPCError(
            code="INVALID_REQUEST",
            message="`params` must be a JSON object.",
        )

    normalized_params = {str(key): value for key, value in params.items()}
    return BridgeRequest(
        request_id=request_id_str,
        method=method,
        params=normalized_params,
    )


def build_response(*, request_id: str, result: JsonObject) -> JsonObject:
    """Build success response payload."""
    return {"id": request_id, "result": result}


def build_error(
    *,
    request_id: str,
    code: str,
    message: str,
    data: Any | None = None,
) -> JsonObject:
    """Build error response payload."""
    error_payload: JsonObject = {"code": code, "message": message}
    if data is not None:
        error_payload["data"] = data
    return {"id": request_id, "error": error_payload}


def build_event(
    *, method: str, params: JsonObject, seq: int | None = None
) -> JsonObject:
    """Build outbound event payload with optional sequence number."""
    event: JsonObject = {"event": method, "params": params}
    if seq is not None:
        event["seq"] = seq
    return event


def write_payload(stream: TextIO, payload: JsonObject) -> None:
    """Write one payload as JSON line and flush."""
    # stdout is the bridge IPC transport channel (not human-readable logging).
    stream.write(
        json.dumps(payload, ensure_ascii=False)
        + "\n"  # codeql[py/clear-text-logging-sensitive-data]
    )
    stream.flush()


class TokenBatcher:
    """Batch assistant_token events with time-based flushing."""

    BATCH_INTERVAL_MS: float = 16.0  # ~60fps, ~62.5 tokens/sec when streaming

    def __init__(self, batch_size: int = 100) -> None:
        self.batch_size = batch_size
        self._tokens: list[str] = []
        self._first_token_time: float | None = None

    def accumulate(self, text: str) -> None:
        """Add token text to the buffer."""
        if text:
            self._tokens.append(text)
        if self._first_token_time is None:
            self._first_token_time = time.monotonic()

    def should_flush(self) -> bool:
        """Check if enough time has elapsed to flush the batch."""
        if self._first_token_time is None:
            return False
        elapsed_ms = (time.monotonic() - self._first_token_time) * 1000
        return elapsed_ms >= self.BATCH_INTERVAL_MS

    def flush(self, seq: int) -> JsonObject | None:
        """Return batched event with accumulated text and token count.

        Returns None if no tokens accumulated.
        """
        if not self._tokens:
            return None
        accumulated = "".join(self._tokens)
        count = len(self._tokens)
        self._tokens = []
        self._first_token_time = None
        return build_event(
            method="chat.event",
            params={
                "kind": "assistant_token_batch",
                "text": accumulated,
                "tokens": count,
            },
            seq=seq,
        )

    def has_tokens(self) -> bool:
        """Check if there are accumulated tokens waiting to be flushed."""
        return len(self._tokens) > 0

    def flush_all(self, seq: int) -> JsonObject | None:
        """Flush all accumulated tokens immediately, ignoring time check."""
        if not self._tokens:
            return None
        accumulated = "".join(self._tokens)
        count = len(self._tokens)
        self._tokens = []
        self._first_token_time = None
        return build_event(
            method="chat.event",
            params={
                "kind": "assistant_token_batch",
                "text": accumulated,
                "tokens": count,
            },
            seq=seq,
        )
