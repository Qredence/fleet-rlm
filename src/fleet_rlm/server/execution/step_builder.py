"""Execution step construction and parent-link derivation helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from .sanitizer import _truncate_text, sanitize_event_payload
from .events import ExecutionStep

ExecutionStepType = Literal["llm", "tool", "repl", "memory", "output"]
ExecutionActorKind = Literal["root_rlm", "sub_agent", "delegate", "unknown"]


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


def _iter_actor_sources(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    step_data = payload.get("step_data")
    if isinstance(step_data, dict):
        return (payload, step_data)
    return (payload,)


def _extract_actor_id(payload: dict[str, Any]) -> str | None:
    for source in _iter_actor_sources(payload):
        for key in ("actor_id", "delegate_id", "sub_agent_id", "agent_id"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _extract_actor_kind(
    payload: dict[str, Any],
    *,
    depth: int | None,
) -> ExecutionActorKind:
    mapped = _extract_actor_kind_from_text(payload)
    if mapped is not None:
        return mapped

    if _is_delegate_execution_profile(payload):
        return "delegate"

    marker_kind = _extract_actor_kind_from_markers(payload)
    if marker_kind is not None:
        return marker_kind

    return _actor_kind_from_depth(depth)


def _map_actor_kind_text(value: str) -> ExecutionActorKind | None:
    lowered = value.strip().lower()
    if not lowered:
        return None
    if lowered in {"root", "root_rlm", "root-rlm", "root agent"}:
        return "root_rlm"
    if lowered in {"sub_agent", "sub-agent", "subagent"}:
        return "sub_agent"
    if lowered in {"delegate", "rlm_delegate", "rlm-delegate"}:
        return "delegate"
    return None


def _extract_actor_kind_from_text(payload: dict[str, Any]) -> ExecutionActorKind | None:
    for source in _iter_actor_sources(payload):
        for key in ("actor_kind", "actor", "agent_kind", "agent_role"):
            value = source.get(key)
            if not isinstance(value, str):
                continue
            mapped = _map_actor_kind_text(value)
            if mapped is not None:
                return mapped
    return None


def _is_delegate_execution_profile(payload: dict[str, Any]) -> bool:
    execution_profile = str(payload.get("execution_profile", "")).strip().upper()
    return execution_profile == "RLM_DELEGATE"


def _has_actor_marker(source: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = source.get(key)
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def _extract_actor_kind_from_markers(
    payload: dict[str, Any],
) -> ExecutionActorKind | None:
    for source in _iter_actor_sources(payload):
        if _has_actor_marker(source, ("delegate_depth", "delegate_id")):
            return "delegate"
        if _has_actor_marker(source, ("sub_agent_depth", "sub_agent_id")):
            return "sub_agent"
    return None


def _actor_kind_from_depth(depth: int | None) -> ExecutionActorKind:
    if depth is None:
        return "unknown"
    return "sub_agent" if depth > 0 else "root_rlm"


def _derive_lane_key(
    actor_kind: ExecutionActorKind,
    actor_id: str | None,
    depth: int | None,
) -> str:
    if actor_id:
        return f"{actor_kind}:{actor_id}"
    if depth is not None:
        return f"{actor_kind}:depth-{depth}"
    return actor_kind


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
        resolved_parent_id = parent_id or self.root_id
        depth = _extract_depth(raw_payload or {})
        actor_kind = _extract_actor_kind(raw_payload or {}, depth=depth)
        if actor_kind == "unknown" and resolved_parent_id == self.root_id:
            actor_kind = "root_rlm"
            if depth is None:
                depth = 0
        actor_id = _extract_actor_id(raw_payload or {})
        step = ExecutionStep(
            id=self._next_id(),
            parent_id=resolved_parent_id,
            type=step_type,
            label=_truncate_text(label),
            depth=depth,
            actor_kind=actor_kind,
            actor_id=actor_id,
            lane_key=_derive_lane_key(actor_kind, actor_id, depth),
            input=sanitize_event_payload(input_payload),
            output=sanitize_event_payload(output_payload),
            timestamp=timestamp,
        )
        if raw_payload is not None:
            self._remember_depth_parent(raw_payload, step.id)
        return step

    def _build_output_like_step(
        self,
        *,
        kind: str,
        text: str,
        payload_obj: dict[str, Any],
        timestamp: float,
    ) -> ExecutionStep:
        label = "assistant_output" if kind == "final" else kind
        return self._build_step(
            step_type="output",
            label=label,
            input_payload={"event_kind": kind},
            output_payload={"text": text, "payload": payload_obj},
            timestamp=timestamp,
            parent_id=self._resolve_parent(payload_obj),
            raw_payload=payload_obj,
        )

    def _build_simple_event_step(
        self,
        *,
        kind: str,
        text: str,
        payload_obj: dict[str, Any],
        timestamp: float,
    ) -> ExecutionStep:
        step_type: ExecutionStepType
        label: str
        if kind == "reasoning_step":
            step_type = "llm"
            label = text or "reasoning"
        elif kind == "plan_update":
            step_type = "llm"
            label = "plan_update"
        elif kind == "rlm_executing":
            step_type = "repl"
            label = "rlm_executing"
        else:
            step_type = "memory"
            label = "memory_update"

        input_payload = (
            payload_obj if kind == "reasoning_step" else {"event_kind": kind}
        )
        output_payload = {"text": text}
        if kind != "reasoning_step":
            output_payload["payload"] = payload_obj

        return self._build_step(
            step_type=step_type,
            label=label,
            input_payload=input_payload,
            output_payload=output_payload,
            timestamp=timestamp,
            parent_id=self._resolve_parent(payload_obj),
            raw_payload=payload_obj,
        )

    def _build_status_step(
        self,
        *,
        text: str,
        payload_obj: dict[str, Any],
        timestamp: float,
    ) -> ExecutionStep | None:
        if not text:
            return None
        if text.startswith("Calling tool:") or text == "Tool finished.":
            return None
        return self._build_step(
            step_type="llm",
            label=text,
            input_payload={"event_kind": "status"},
            output_payload={"text": text},
            timestamp=timestamp,
            parent_id=self._resolve_parent(payload_obj),
            raw_payload=payload_obj,
        )

    def _build_tool_call_step(
        self,
        *,
        text: str,
        payload_obj: dict[str, Any],
        timestamp: float,
    ) -> ExecutionStep:
        tool_name = _extract_tool_name(text, payload_obj)
        step = self._build_step(
            step_type=_tool_step_type(tool_name),
            label=tool_name or text or "tool_call",
            input_payload=payload_obj,
            output_payload=None,
            timestamp=timestamp,
            parent_id=self._resolve_parent(payload_obj),
            raw_payload=payload_obj,
        )
        self._last_tool_step_id = step.id
        return step

    def _build_tool_result_step(
        self,
        *,
        text: str,
        payload_obj: dict[str, Any],
        timestamp: float,
    ) -> ExecutionStep:
        tool_name = _extract_tool_name(text, payload_obj)
        parent_id = self._last_tool_step_id or self._resolve_parent(payload_obj)
        return self._build_step(
            step_type=_tool_step_type(tool_name),
            label=(tool_name or text or "tool_result"),
            input_payload={"event_kind": "tool_result", "tool_name": tool_name},
            output_payload=payload_obj,
            timestamp=timestamp,
            parent_id=parent_id,
            raw_payload=payload_obj,
        )

    def _build_trajectory_step(
        self,
        *,
        text: str,
        payload_obj: dict[str, Any],
        timestamp: float,
    ) -> ExecutionStep:
        step_data = payload_obj.get("step_data")
        step_dict = step_data if isinstance(step_data, dict) else {}
        tool_name = _extract_tool_name(text, step_dict)
        label = (
            tool_name
            or str(step_dict.get("thought", "")).strip()
            or str(step_dict.get("action", "")).strip()
            or text
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
            return self._build_status_step(
                text=stripped_text,
                payload_obj=payload_obj,
                timestamp=timestamp,
            )

        if kind in {"reasoning_step", "plan_update", "rlm_executing", "memory_update"}:
            return self._build_simple_event_step(
                kind=kind,
                text=stripped_text,
                payload_obj=payload_obj,
                timestamp=timestamp,
            )

        if kind == "tool_call":
            return self._build_tool_call_step(
                text=stripped_text,
                payload_obj=payload_obj,
                timestamp=timestamp,
            )

        if kind == "tool_result":
            return self._build_tool_result_step(
                text=stripped_text,
                payload_obj=payload_obj,
                timestamp=timestamp,
            )

        if kind == "trajectory_step":
            return self._build_trajectory_step(
                text=stripped_text,
                payload_obj=payload_obj,
                timestamp=timestamp,
            )

        if kind in {"final", "cancelled", "error"}:
            return self._build_output_like_step(
                kind=kind,
                text=stripped_text,
                payload_obj=payload_obj,
                timestamp=timestamp,
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
                raw_payload=payload,
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
                raw_payload=payload,
            )

        return None
