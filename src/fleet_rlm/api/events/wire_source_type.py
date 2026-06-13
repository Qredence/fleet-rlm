"""Shared wire ``source_type`` derivation for chat websocket frames."""

from __future__ import annotations

from typing import Any

from fleet_rlm.runtime.events import RuntimeEventKind

_BACKEND_KIND_VALUES = frozenset(
    {
        "turn_started",
        "status",
        "reasoning",
        "tool_call",
        "tool_result",
        "sandbox_exec",
        "rlm_delegate",
        "warning",
        "clarification",
        "text",
        "turn_completed",
        "turn_failed",
    }
)

_KIND_ALIASES: dict[RuntimeEventKind, str] = {
    RuntimeEventKind.TURN_STARTED: "turn_started",
    RuntimeEventKind.DONE: "turn_completed",
    RuntimeEventKind.ERROR: "turn_failed",
}


def _as_str(value: Any) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    return None


def _normalize_kind(kind: RuntimeEventKind | str) -> str:
    if isinstance(kind, RuntimeEventKind):
        return kind.value
    return str(kind)


def derive_wire_source_type(kind: RuntimeEventKind | str, payload: dict[str, Any] | None = None) -> str:
    """Return the wire ``payload.source_type`` for one runtime/chat event."""
    payload_obj = dict(payload or {})
    kind_value = _normalize_kind(kind)

    if kind_value == "done":
        return "turn_completed"
    if kind_value == "error":
        return "turn_failed"
    if kind_value == "status":
        phase = _as_str(payload_obj.get("phase"))
        if phase == "sandbox_exec":
            return "sandbox_exec"
        if phase == "delegate" or payload_obj.get("delegate") is True:
            return "rlm_delegate"

    if isinstance(kind, RuntimeEventKind):
        return _KIND_ALIASES.get(kind, kind.value)

    if kind_value in _BACKEND_KIND_VALUES:
        return kind_value
    return "text"


__all__ = ["derive_wire_source_type"]
