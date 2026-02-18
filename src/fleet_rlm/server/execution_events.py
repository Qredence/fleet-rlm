"""Structured execution-event models and streaming helpers.

This module powers the dedicated ``/ws/execution`` event stream consumed by
Artifact Canvas-style visualizations.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Literal

from fastapi import WebSocket
from pydantic import BaseModel

ExecutionStepType = Literal["llm", "tool", "repl", "memory", "output"]
ExecutionEventType = Literal[
    "execution_started",
    "execution_step",
    "execution_completed",
]

_SENSITIVE_KEYWORDS = (
    "token",
    "secret",
    "api_key",
    "password",
    "authorization",
)
_MAX_TEXT_CHARS = 2048
_MAX_COLLECTION_ITEMS = 50
_MAX_RECURSION_DEPTH = 6


class ExecutionStep(BaseModel):
    """Single execution graph node/edge payload."""

    id: str
    parent_id: str | None = None
    type: ExecutionStepType
    label: str
    input: Any | None = None
    output: Any | None = None
    timestamp: float


class ExecutionEvent(BaseModel):
    """Top-level event envelope emitted over ``/ws/execution``."""

    type: ExecutionEventType
    run_id: str
    workspace_id: str
    user_id: str
    session_id: str
    step: ExecutionStep | None = None


class ExecutionSubscription(BaseModel):
    """Required identity filter for execution-stream subscriptions."""

    workspace_id: str
    user_id: str
    session_id: str

    def matches(self, event: ExecutionEvent) -> bool:
        return (
            self.workspace_id == event.workspace_id
            and self.user_id == event.user_id
            and self.session_id == event.session_id
        )


def _truncate_text(text: str, *, max_chars: int = _MAX_TEXT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated]"


def _looks_sensitive_key(key: str) -> bool:
    lowered = key.strip().lower()
    return any(token in lowered for token in _SENSITIVE_KEYWORDS)


def sanitize_event_payload(value: Any, *, depth: int = 0) -> Any:
    """Truncate large payloads and redact sensitive values for websocket emission."""
    if depth >= _MAX_RECURSION_DEPTH:
        return "<max-depth>"

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate_text(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, dict):
        items = list(value.items())
        sanitized: dict[str, Any] = {}
        for index, (key, raw) in enumerate(items):
            if index >= _MAX_COLLECTION_ITEMS:
                sanitized["__truncated__"] = len(items) - _MAX_COLLECTION_ITEMS
                break
            key_str = str(key)
            if _looks_sensitive_key(key_str):
                sanitized[key_str] = "<redacted>"
            else:
                sanitized[key_str] = sanitize_event_payload(raw, depth=depth + 1)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        sequence = list(value)
        limited = [
            sanitize_event_payload(item, depth=depth + 1)
            for item in sequence[:_MAX_COLLECTION_ITEMS]
        ]
        if len(sequence) > _MAX_COLLECTION_ITEMS:
            limited.append(f"<truncated:{len(sequence) - _MAX_COLLECTION_ITEMS}>")
        return limited
    return _truncate_text(str(value))


def summarize_code_for_event(code: str) -> dict[str, str]:
    """Build stable code metadata for REPL execution events."""
    normalized = code or ""
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    compact = re.sub(r"\s+", " ", normalized).strip()
    return {"code_hash": digest, "code_preview": _truncate_text(compact, max_chars=240)}


class ExecutionEventEmitter:
    """Broadcast ``ExecutionEvent`` payloads to matching websocket subscribers."""

    def __init__(self) -> None:
        self._connections: dict[WebSocket, ExecutionSubscription] = {}
        self._lock = RLock()

    async def connect(
        self, websocket: WebSocket, subscription: ExecutionSubscription
    ) -> None:
        await websocket.accept()
        with self._lock:
            self._connections[websocket] = subscription

    async def disconnect(self, websocket: WebSocket) -> None:
        with self._lock:
            self._connections.pop(websocket, None)

    async def emit(self, event: ExecutionEvent) -> None:
        payload = event.model_dump(mode="json")
        with self._lock:
            targets = [
                websocket
                for websocket, subscription in self._connections.items()
                if subscription.matches(event)
            ]

        stale: list[WebSocket] = []
        for websocket in targets:
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)

        if stale:
            with self._lock:
                for websocket in stale:
                    self._connections.pop(websocket, None)


def _extract_depth(payload: dict[str, Any]) -> int | None:
    candidates: list[Any] = [
        payload.get("depth"),
        payload.get("delegate_depth"),
        payload.get("sub_agent_depth"),
    ]
    step_data = payload.get("step_data")
    if isinstance(step_data, dict):
        candidates.extend(
            [
                step_data.get("depth"),
                step_data.get("delegate_depth"),
                step_data.get("sub_agent_depth"),
            ]
        )
    for raw in candidates:
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            return max(0, int(raw))
        if isinstance(raw, str) and raw.isdigit():
            return int(raw)
    return None


def _extract_parent_hint(payload: dict[str, Any]) -> str | None:
    for key in ("parent_step_id", "parent_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    step_data = payload.get("step_data")
    if isinstance(step_data, dict):
        for key in ("parent_step_id", "parent_id"):
            value = step_data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _extract_tool_name(text: str, payload: dict[str, Any]) -> str | None:
    raw_name = payload.get("tool_name")
    if isinstance(raw_name, str) and raw_name.strip():
        return raw_name.strip()

    status_message = str(payload.get("raw_status", text or "")).strip()
    if status_message.startswith("Calling tool:"):
        return status_message.removeprefix("Calling tool:").strip().split("(", 1)[0]
    if text.startswith("tool call:"):
        return text.removeprefix("tool call:").strip().split("(", 1)[0]
    return None


def _tool_step_type(tool_name: str | None) -> ExecutionStepType:
    if not tool_name:
        return "tool"
    lowered = tool_name.lower()
    if lowered.startswith("memory_") or lowered.startswith("core_memory_"):
        return "memory"
    return "tool"


@dataclass(slots=True)
class ExecutionStepBuilder:
    """Deterministic step-id and parent-link builder for one execution run."""

    run_id: str
    root_id: str = field(init=False)
    _counter: int = 0
    _last_tool_step_id: str | None = None
    _depth_parents: dict[int, str] = field(default_factory=dict)
    _repl_parent_by_hash: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root_id = f"{self.run_id}:root"
        self._depth_parents[0] = self.root_id

    def _next_id(self) -> str:
        self._counter += 1
        return f"{self.run_id}:s{self._counter}"

    def _resolve_parent(self, payload: dict[str, Any]) -> str:
        parent_hint = _extract_parent_hint(payload)
        if parent_hint:
            return parent_hint

        depth = _extract_depth(payload)
        if depth is None:
            return self.root_id

        if depth <= 0:
            return self.root_id
        return self._depth_parents.get(depth - 1, self.root_id)

    def _remember_depth_parent(self, payload: dict[str, Any], step_id: str) -> None:
        depth = _extract_depth(payload)
        if depth is not None:
            self._depth_parents[depth] = step_id

    def _build_step(
        self,
        *,
        step_type: ExecutionStepType,
        label: str,
        input_payload: Any,
        output_payload: Any,
        timestamp: float,
        parent_id: str | None = None,
        raw_payload: dict[str, Any] | None = None,
    ) -> ExecutionStep:
        step = ExecutionStep(
            id=self._next_id(),
            parent_id=parent_id or self.root_id,
            type=step_type,
            label=_truncate_text(label, max_chars=200),
            input=sanitize_event_payload(input_payload),
            output=sanitize_event_payload(output_payload),
            timestamp=timestamp,
        )
        if raw_payload is not None:
            self._remember_depth_parent(raw_payload, step.id)
        return step

    def from_stream_event(
        self,
        *,
        kind: str,
        text: str,
        payload: dict[str, Any] | None,
        timestamp: float,
    ) -> ExecutionStep | None:
        payload_obj = payload if isinstance(payload, dict) else {}
        stripped_text = (text or "").strip()

        if kind == "assistant_token":
            return None

        if kind == "status":
            if not stripped_text:
                return None
            if (
                stripped_text.startswith("Calling tool:")
                or stripped_text == "Tool finished."
            ):
                return None
            return self._build_step(
                step_type="llm",
                label=stripped_text,
                input_payload={"event_kind": kind},
                output_payload={"text": stripped_text},
                timestamp=timestamp,
                parent_id=self._resolve_parent(payload_obj),
                raw_payload=payload_obj,
            )

        if kind == "reasoning_step":
            return self._build_step(
                step_type="llm",
                label=stripped_text or "reasoning",
                input_payload=payload_obj or {"event_kind": kind},
                output_payload={"text": stripped_text},
                timestamp=timestamp,
                parent_id=self._resolve_parent(payload_obj),
                raw_payload=payload_obj,
            )

        if kind == "tool_call":
            tool_name = _extract_tool_name(stripped_text, payload_obj)
            step = self._build_step(
                step_type=_tool_step_type(tool_name),
                label=tool_name or stripped_text or "tool_call",
                input_payload=payload_obj,
                output_payload=None,
                timestamp=timestamp,
                parent_id=self._resolve_parent(payload_obj),
                raw_payload=payload_obj,
            )
            self._last_tool_step_id = step.id
            return step

        if kind == "tool_result":
            tool_name = _extract_tool_name(stripped_text, payload_obj)
            parent_id = self._last_tool_step_id or self._resolve_parent(payload_obj)
            return self._build_step(
                step_type=_tool_step_type(tool_name),
                label=(tool_name or stripped_text or "tool_result"),
                input_payload={"event_kind": kind, "tool_name": tool_name},
                output_payload=payload_obj,
                timestamp=timestamp,
                parent_id=parent_id,
                raw_payload=payload_obj,
            )

        if kind == "trajectory_step":
            step_data = payload_obj.get("step_data")
            step_dict = step_data if isinstance(step_data, dict) else {}
            tool_name = _extract_tool_name(stripped_text, step_dict)
            label = (
                tool_name
                or str(step_dict.get("thought", "")).strip()
                or str(step_dict.get("action", "")).strip()
                or stripped_text
                or "trajectory_step"
            )
            return self._build_step(
                step_type=_tool_step_type(tool_name) if tool_name else "llm",
                label=label,
                input_payload=step_dict.get("input", step_dict),
                output_payload=step_dict.get("output", step_dict.get("observation")),
                timestamp=timestamp,
                parent_id=self._resolve_parent(payload_obj),
                raw_payload=payload_obj,
            )

        if kind == "final":
            return self._build_step(
                step_type="output",
                label="assistant_output",
                input_payload={"event_kind": kind},
                output_payload={"text": stripped_text, "payload": payload_obj},
                timestamp=timestamp,
                parent_id=self._resolve_parent(payload_obj),
                raw_payload=payload_obj,
            )

        if kind in {"cancelled", "error"}:
            return self._build_step(
                step_type="output",
                label=kind,
                input_payload={"event_kind": kind},
                output_payload={"text": stripped_text, "payload": payload_obj},
                timestamp=timestamp,
                parent_id=self._resolve_parent(payload_obj),
                raw_payload=payload_obj,
            )

        return None

    def from_interpreter_hook(self, payload: dict[str, Any]) -> ExecutionStep | None:
        if not isinstance(payload, dict):
            return None
        phase = str(payload.get("phase", "")).strip().lower()
        timestamp_raw = payload.get("timestamp", time.time())
        try:
            timestamp = float(timestamp_raw)
        except (TypeError, ValueError):
            timestamp = time.time()

        code_hash = str(payload.get("code_hash", "")).strip()
        if phase == "start":
            step = self._build_step(
                step_type="repl",
                label="repl_execute",
                input_payload=payload,
                output_payload=None,
                timestamp=timestamp,
                parent_id=self.root_id,
                raw_payload={},
            )
            if code_hash:
                self._repl_parent_by_hash[code_hash] = step.id
            return step

        if phase == "complete":
            parent_id = self._repl_parent_by_hash.pop(code_hash, None) or self.root_id
            return self._build_step(
                step_type="repl",
                label="repl_result",
                input_payload={"event_kind": "repl_complete", "code_hash": code_hash},
                output_payload=payload,
                timestamp=timestamp,
                parent_id=parent_id,
                raw_payload={},
            )

        return None
