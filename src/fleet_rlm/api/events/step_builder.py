"""Execution step construction and parent-link derivation helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .events import ExecutionStep
from .sanitizer import _truncate_text, sanitize_event_payload
from .step_builder_extractors import (
    ExecutionStepType,
    _derive_lane_key,
    _extract_actor_id,
    _extract_actor_kind,
    _extract_depth,
    _extract_parent_hint,
)
from .step_builder_mapping import (
    build_output_like_spec,
    build_simple_event_spec,
    build_status_spec,
    build_tool_call_spec,
    build_tool_result_spec,
    build_trajectory_spec,
)


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
        step_type, label, input_payload, output_payload = build_output_like_spec(
            kind=kind,
            text=text,
            payload_obj=payload_obj,
        )
        return self._build_step(
            step_type=step_type,
            label=label,
            input_payload=input_payload,
            output_payload=output_payload,
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
        step_type, label, input_payload, output_payload = build_simple_event_spec(
            kind=kind,
            text=text,
            payload_obj=payload_obj,
        )

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
        spec = build_status_spec(text)
        if spec is None:
            return None
        step_type, label, input_payload, output_payload = spec
        return self._build_step(
            step_type=step_type,
            label=label,
            input_payload=input_payload,
            output_payload=output_payload,
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
        step_type, label, input_payload, output_payload, _tool_name = (
            build_tool_call_spec(text=text, payload_obj=payload_obj)
        )
        step = self._build_step(
            step_type=step_type,
            label=label,
            input_payload=input_payload,
            output_payload=output_payload,
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
        step_type, label, input_payload, output_payload, _tool_name = (
            build_tool_result_spec(text=text, payload_obj=payload_obj)
        )
        parent_id = self._last_tool_step_id or self._resolve_parent(payload_obj)
        return self._build_step(
            step_type=step_type,
            label=label,
            input_payload=input_payload,
            output_payload=output_payload,
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
        step_type, label, input_payload, output_payload = build_trajectory_spec(
            text=text,
            payload_obj=payload_obj,
        )
        return self._build_step(
            step_type=step_type,
            label=label,
            input_payload=input_payload,
            output_payload=output_payload,
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

        if kind in {"status", "warning"}:
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

        if phase == "progress":
            parent_id = self._repl_parent_by_hash.get(code_hash, self.root_id)
            event_kind = str(payload.get("event_kind", "")).strip() or "progress"
            path = str(payload.get("path", "")).strip()
            bytes_written = payload.get("bytes_written")
            bytes_total = payload.get("bytes_total")
            label = event_kind
            if path:
                label = f"{event_kind}: {path}"
            return self._build_step(
                step_type="repl",
                label=label,
                input_payload={"event_kind": event_kind, "code_hash": code_hash},
                output_payload={
                    "path": path or None,
                    "bytes_written": bytes_written,
                    "bytes_total": bytes_total,
                    "payload": payload,
                },
                timestamp=timestamp,
                parent_id=parent_id,
                raw_payload=payload,
            )

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
