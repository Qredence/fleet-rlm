"""Chat-frame projector: RuntimeEvent → websocket chat payload.

One structured event in, one deterministic dict out — no text re-parsing,
no intermediate ``BackendEvent`` hop, no timestamp-derived ``event_id``.

Usage::

    from fleet_rlm.api.events.project_chat import project_chat

    frame = project_chat(runtime_event, sequence=42, run_id="run-abc")
"""

from __future__ import annotations

from typing import Any

from fleet_rlm.runtime.events import EVENT_SCHEMA_VERSION, RuntimeEvent, RuntimeEventKind

from .wire_source_type import derive_wire_source_type

_TURN_STARTED_KINDS: frozenset[RuntimeEventKind] = frozenset({RuntimeEventKind.TURN_STARTED})
_TERMINAL_KINDS: frozenset[RuntimeEventKind] = frozenset({RuntimeEventKind.DONE, RuntimeEventKind.ERROR})


def _frame_kind(event_kind: RuntimeEventKind) -> str:
    if event_kind in _TURN_STARTED_KINDS:
        return "execution_started"
    if event_kind in _TERMINAL_KINDS:
        return "execution_completed"
    return "execution_step"


def project_chat(
    event: RuntimeEvent,
    *,
    sequence: int = 0,
    run_id: str | None = None,
    payload_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project one ``RuntimeEvent`` to a websocket chat frame dict.

    Args:
        event: The canonical runtime event to project.
        sequence: Monotonic per-turn counter, used as the ``event_id``.
        run_id: Optional run identifier prefixed to ``event_id``.
        payload_override: Optional enriched payload merged before projection.

    Returns:
        A dict ready for ``websocket.send_json()``.
    """
    payload: dict[str, Any] = dict(event.payload)
    if payload_override is not None:
        payload.update(payload_override)
    payload.setdefault("source_type", derive_wire_source_type(event.kind, payload))

    if event.context is not None:
        payload["runtime"] = event.context.model_dump(mode="json", exclude_none=True)

    if event.tool is not None and event.kind in {RuntimeEventKind.TOOL_CALL, RuntimeEventKind.TOOL_RESULT}:
        payload.setdefault("tool_name", event.tool.tool_name)
        if event.tool.tool_args:
            payload.setdefault("tool_args", event.tool.tool_args)
        if event.tool.tool_input is not None:
            payload.setdefault("tool_input", event.tool.tool_input)
        if event.tool.tool_output is not None:
            payload.setdefault("tool_output", event.tool.tool_output)
        if event.tool.step_index is not None:
            payload.setdefault("step_index", event.tool.step_index)

    frame_kind = _frame_kind(event.kind)
    if frame_kind == "execution_completed":
        payload.setdefault(
            "status",
            "failed" if event.kind == RuntimeEventKind.ERROR else "completed",
        )

    event_id = f"{run_id}:{sequence}" if run_id else str(sequence)

    return {
        "kind": frame_kind,
        "text": event.text,
        "payload": payload,
        "timestamp": event.timestamp.isoformat(),
        "version": EVENT_SCHEMA_VERSION,
        "event_id": event_id,
        "sequence": sequence,
    }


__all__ = ["project_chat"]
