"""Shared runtime-event emission helpers for Daytona host-loop runs."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .protocol import RunEventFrame
from .sandbox import DaytonaSandboxSession
from .types import AgentNode, RolloutBudget


class DaytonaRuntimeEventEmitter:
    """Build and emit normalized Daytona runtime events."""

    def __init__(
        self,
        *,
        emit_runtime_event: Callable[[RunEventFrame], None],
        session: DaytonaSandboxSession,
        budget: RolloutBudget,
        run_id: str,
        request_id: str,
        started_at: float,
        active_iteration_getter: Callable[[], int | None],
        volume_name: str | None = None,
    ) -> None:
        self._emit_runtime_event = emit_runtime_event
        self._session = session
        self._budget = budget
        self._run_id = run_id
        self._request_id = request_id
        self._started_at = started_at
        self._active_iteration_getter = active_iteration_getter
        self._volume_name = volume_name

    def phase_timings_ms(self) -> dict[str, int]:
        timings = getattr(self._session, "phase_timings_ms", {}) or {}
        return {
            str(key): int(value)
            for key, value in timings.items()
            if key is not None and value is not None
        }

    def runtime_payload(self, node: AgentNode) -> dict[str, Any]:
        return {
            "runtime_mode": "daytona_pilot",
            "daytona_mode": "host_loop_rlm",
            "run_id": self._run_id,
            "depth": node.depth,
            "max_depth": self._budget.max_depth,
            "effective_max_iters": self._budget.max_iterations,
            "sandbox_active": node.sandbox_id is not None,
            "sandbox_id": node.sandbox_id,
            "execution_profile": "DAYTONA_PILOT_HOST_LOOP",
            "phase_timings_ms": self.phase_timings_ms(),
            "volume_name": self._volume_name,
        }

    def progress_payload(
        self,
        *,
        iteration: int | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "elapsed_ms": int((time.monotonic() - self._started_at) * 1000),
        }
        if iteration is not None:
            payload["iteration"] = iteration
        if extra_payload:
            payload.update(extra_payload)
        return payload

    @staticmethod
    def node_payload(node: AgentNode) -> dict[str, Any]:
        payload = node.to_dict()
        payload["prompt_manifest"] = {
            "handles": [handle.to_dict() for handle in node.prompt_handles]
        }
        return payload

    def emit_frame(
        self,
        *,
        node: AgentNode,
        kind: str,
        text: str,
        phase: str | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "runtime_mode": "daytona_pilot",
            "phase": phase,
            "status": node.status,
            "node_id": node.node_id,
            "node": self.node_payload(node),
            "runtime": self.runtime_payload(node),
        }
        if extra_payload:
            payload.update(extra_payload)
        self._emit_runtime_event(
            RunEventFrame(
                request_id=self._request_id,
                kind=kind,
                text=text,
                payload={
                    key: value for key, value in payload.items() if value is not None
                },
            )
        )

    def emit_status(self, node: AgentNode, text: str, *, phase: str) -> None:
        self.emit_frame(node=node, kind="status", text=text, phase=phase)

    def emit_progress_status(
        self,
        node: AgentNode,
        text: str,
        *,
        phase: str,
        iteration: int | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        self.emit_frame(
            node=node,
            kind="status",
            text=text,
            phase=phase,
            extra_payload=self.progress_payload(
                iteration=iteration,
                extra_payload=extra_payload,
            ),
        )

    def emit_warning(self, node: AgentNode, text: str, *, phase: str) -> None:
        self.emit_frame(
            node=node,
            kind="warning",
            text=text,
            phase=phase,
            extra_payload={"warning": text},
        )

    def emit_error(self, node: AgentNode, text: str, *, phase: str) -> None:
        self.emit_frame(node=node, kind="error", text=text, phase=phase)

    def emit_cancelled(self, node: AgentNode, text: str, *, phase: str) -> None:
        self.emit_frame(node=node, kind="cancelled", text=text, phase=phase)

    def emit_reasoning_step(
        self,
        node: AgentNode,
        text: str,
        *,
        phase: str,
        iteration: int | None = None,
        label: str | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        payload = self.progress_payload(
            iteration=iteration,
            extra_payload=extra_payload,
        )
        if label is not None:
            payload["reasoning_label"] = label
        self.emit_frame(
            node=node,
            kind="reasoning_step",
            text=text,
            phase=phase,
            extra_payload=payload,
        )

    def emit_trajectory_step(
        self,
        node: AgentNode,
        text: str,
        *,
        phase: str,
        step_index: int,
        iteration: int | None = None,
        thought: str | None = None,
        action: str | None = None,
        tool_name: str | None = None,
        tool_input: Any | None = None,
        observation: Any | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        payload = self.progress_payload(
            iteration=iteration,
            extra_payload=extra_payload,
        )
        payload["step_index"] = step_index
        step_data = {
            "index": step_index,
            "thought": thought,
            "action": action,
            "tool_name": tool_name,
            "input": tool_input,
            "observation": observation,
        }
        payload["step_data"] = {
            key: value for key, value in step_data.items() if value is not None
        }
        self.emit_frame(
            node=node,
            kind="trajectory_step",
            text=text,
            phase=phase,
            extra_payload=payload,
        )

    def emit_tool_call(
        self, node: AgentNode, callback_name: str, tool_input: dict[str, Any]
    ) -> None:
        self.emit_frame(
            node=node,
            kind="tool_call",
            text=f"Running host callback `{callback_name}`.",
            phase="host_callback",
            extra_payload={
                "iteration": self._active_iteration_getter(),
                "callback_name": callback_name,
                "tool_input": tool_input,
            },
        )

    def emit_tool_result(
        self,
        node: AgentNode,
        callback_name: str,
        value: dict[str, Any],
        *,
        tool_input: dict[str, Any] | None = None,
    ) -> None:
        self.emit_frame(
            node=node,
            kind="tool_result",
            text=f"Completed host callback `{callback_name}`.",
            phase="host_callback",
            extra_payload={
                "iteration": self._active_iteration_getter(),
                "callback_name": callback_name,
                "tool_input": tool_input,
                "tool_result": value,
            },
        )
