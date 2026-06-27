"""Execution-graph projector: RuntimeEvent → ExecutionStep.

Reads typed fields (``event.tool``, ``event.kind``, ``event.context``) directly —
no ``_extract_tool_name`` text parsing, no ``_extract_actor_kind`` dict scraping.

Usage::

    from fleet_rlm.api.events.project_graph import project_graph

    step = project_graph(runtime_event, builder)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind

from .sanitizer import sanitize_event_payload
from .step_builder_extractors import (
    ExecutionActorKind,
    ExecutionStepType,
    _derive_lane_key,
    _map_actor_kind_text,
    _tool_step_type,
)

if TYPE_CHECKING:
    from .step_builder import ExecutionStepBuilder


def _step_type_from_kind(kind: RuntimeEventKind, tool_name: str | None) -> ExecutionStepType:
    if kind == RuntimeEventKind.TURN_INPUTS:
        return "turn_inputs"  # type: ignore[return-value]
    if kind in {RuntimeEventKind.TOOL_CALL, RuntimeEventKind.TOOL_RESULT}:
        return _tool_step_type(tool_name)
    if kind in {RuntimeEventKind.DONE, RuntimeEventKind.ERROR}:
        return "output"
    return "llm"


def _label_from_event(event: RuntimeEvent) -> str | None:
    kind = event.kind
    if kind in {RuntimeEventKind.TOOL_CALL, RuntimeEventKind.TOOL_RESULT}:
        return event.tool.tool_name if event.tool else (event.payload.get("tool_name") or event.text or kind.value)
    if kind == RuntimeEventKind.DONE:
        return "assistant_output"
    if kind == RuntimeEventKind.ERROR:
        return "error"
    if kind == RuntimeEventKind.REASONING:
        return event.text or "reasoning"
    if kind == RuntimeEventKind.TEXT:
        return "assistant_token"
    if kind == RuntimeEventKind.TURN_INPUTS:
        return "turn_inputs"
    if kind in {RuntimeEventKind.STATUS, RuntimeEventKind.WARNING}:
        stripped = event.text.strip()
        if not stripped:
            return None
        if stripped in {"Calling tool:", "Tool finished."}:
            return None
        return stripped
    return kind.value


def _serialize_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """Serialize TurnInputRow models or pre-serialized dicts to JSON-safe dicts."""
    result: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            result.append(row)
        elif hasattr(row, "model_dump"):
            result.append(row.model_dump(mode="json"))
        else:
            result.append(
                {"label": str(getattr(row, "label", "")), "kind": str(getattr(row, "kind", "")), "value": str(row)}
            )
    return result


def _input_for_kind(event: RuntimeEvent) -> Any:
    kind = event.kind
    if kind == RuntimeEventKind.TOOL_CALL:
        if event.tool:
            return {"tool_name": event.tool.tool_name, "tool_args": event.tool.tool_args}
        return dict(event.payload)
    if kind == RuntimeEventKind.TOOL_RESULT:
        return {"event_kind": "tool_result", "tool_name": event.tool.tool_name if event.tool else None}
    if kind == RuntimeEventKind.TEXT:
        return {"event_kind": "text"}
    if kind == RuntimeEventKind.TURN_INPUTS:
        return {"rows": _serialize_rows(event.payload.get("rows", []))}
    if kind in {RuntimeEventKind.STATUS, RuntimeEventKind.WARNING}:
        return {"event_kind": kind.value}
    if kind in {RuntimeEventKind.DONE, RuntimeEventKind.ERROR}:
        return {"event_kind": kind.value}
    return dict(event.payload)


def _output_for_kind(event: RuntimeEvent) -> Any:
    kind = event.kind
    if kind == RuntimeEventKind.TOOL_CALL:
        return None
    if kind == RuntimeEventKind.TOOL_RESULT:
        if event.tool and event.tool.tool_output is not None:
            return event.tool.tool_output
        return dict(event.payload)
    if kind == RuntimeEventKind.TURN_INPUTS:
        return {"rows": _serialize_rows(event.payload.get("rows", []))}
    if kind == RuntimeEventKind.TEXT:
        return {"text": event.text}
    if kind == RuntimeEventKind.REASONING:
        return {"text": event.text}
    if kind in {RuntimeEventKind.DONE, RuntimeEventKind.ERROR}:
        return {"text": event.text, "payload": dict(event.payload)}
    return {"text": event.text} if event.text else None


def project_graph(event: RuntimeEvent, builder: ExecutionStepBuilder) -> Any:
    """Project one ``RuntimeEvent`` to an ``ExecutionStep``.

    Step type and label are derived from typed ``RuntimeEvent`` fields — no
    text parsing.  The builder is responsible only for ID generation and
    parent-link tracking.

    Args:
        event: The canonical runtime event to project.
        builder: Per-turn step builder providing IDs and parent links.

    Returns:
        A constructed :class:`~fleet_rlm.api.events.events.ExecutionStep`, or
        ``None`` if this event kind should produce no graph node.
    """
    from .events import ExecutionStep

    label = _label_from_event(event)
    if label is None:
        return None

    kind = event.kind
    tool_name = event.tool.tool_name if event.tool else None
    step_type = _step_type_from_kind(kind, tool_name)

    ctx = event.context
    depth = ctx.depth if ctx else None
    actor_kind_raw = ctx.actor_kind if ctx else None
    actor_id = ctx.actor_id if ctx else None
    parent_id_hint = ctx.parent_id if ctx else None

    mapped_actor_kind = _map_actor_kind_text(actor_kind_raw) if actor_kind_raw else None
    actor_kind: ExecutionActorKind = mapped_actor_kind or "unknown"
    if actor_kind == "unknown" and depth is None:
        actor_kind = "root_rlm"
        depth = 0

    lane_key = _derive_lane_key(actor_kind, actor_id, depth)

    payload_dict: dict[str, Any] = dict(event.payload)
    resolved_parent_id = parent_id_hint or builder._resolve_parent(payload_dict)

    input_payload = sanitize_event_payload(_input_for_kind(event))
    output_payload = sanitize_event_payload(_output_for_kind(event))

    from .sanitizer import _truncate_text

    step = ExecutionStep(
        id=builder._next_id(),
        parent_id=resolved_parent_id,
        type=step_type,
        label=_truncate_text(label),
        depth=depth,
        actor_kind=actor_kind,
        actor_id=actor_id,
        lane_key=lane_key,
        input=input_payload,
        output=output_payload,
        timestamp=event.timestamp.timestamp(),
    )

    if depth is not None:
        builder._depth_parents[depth] = step.id
    if kind == RuntimeEventKind.TOOL_CALL:
        builder._last_tool_step_id = step.id

    return step


__all__ = ["project_graph"]
