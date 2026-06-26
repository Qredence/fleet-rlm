"""Canonical runtime event model — single source of truth for all streaming events.

All structured event data is carried forward from the point of construction;
no re-parsing of rendered text downstream.

Usage::

    from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind

    event = RuntimeEvent(
        kind=RuntimeEventKind.TOOL_CALL,
        text="Calling repl_execute(...)",
        tool=RuntimeToolInfo(tool_name="repl_execute", tool_args={"code": "..."}),
    )
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

EVENT_SCHEMA_VERSION: int = 3

_MLFLOW_SPAN_STARTED_STATUSES = frozenset(
    {"started", "running", "pending", "in_progress", "in-progress", "in progress"}
)
_MLFLOW_SPAN_COMPLETED_STATUSES = frozenset(
    {"completed", "complete", "success", "succeeded", "ok", "status_code_ok", "statuscode.ok"}
)
_MLFLOW_SPAN_ERROR_STATUSES = frozenset(
    {"error", "errored", "failed", "failure", "fail", "status_code_error", "statuscode.error"}
)


def _normalize_mlflow_span_status(
    status: str,
    *,
    duration_ms: int | float | None = None,
    ended_at: str | None = None,
    output: Any | None = None,
    error: Any | None = None,
) -> tuple[str, str | None]:
    """Return the internal span lifecycle status and optional raw external status."""
    raw_status = str(status or "").strip()
    status_key = raw_status.lower()

    if status_key in _MLFLOW_SPAN_STARTED_STATUSES:
        normalized_status = "started"
    elif status_key in _MLFLOW_SPAN_COMPLETED_STATUSES:
        normalized_status = "completed"
    elif status_key in _MLFLOW_SPAN_ERROR_STATUSES:
        normalized_status = "error"
    elif error is not None:
        normalized_status = "error"
    elif ended_at or duration_ms is not None or output is not None:
        normalized_status = "completed"
    else:
        normalized_status = "started"

    raw_status_payload = raw_status if raw_status and status_key != normalized_status else None
    return normalized_status, raw_status_payload


class RuntimeEventKind(str, Enum):
    """All event kinds emitted by the runtime streaming pipeline."""

    STATUS = "status"
    TEXT = "text"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    WARNING = "warning"
    ERROR = "error"
    DONE = "done"
    CLARIFICATION = "clarification"
    TURN_STARTED = "turn_started"
    SANDBOX_EXEC = "sandbox_exec"
    RLM_DELEGATE = "rlm_delegate"
    MLFLOW_SPAN = "mlflow_span"
    TURN_INPUTS = "turn_inputs"

    @classmethod
    def terminal_kinds(cls) -> frozenset[RuntimeEventKind]:
        return frozenset({cls.DONE, cls.ERROR})

    def is_terminal(self) -> bool:
        return self in self.terminal_kinds()


class RuntimeToolInfo(BaseModel):
    """Structured tool call / result data — never re-parsed from display text."""

    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    tool_input: str | None = None
    tool_output: Any | None = None
    step_index: int | None = None


class TurnInputRow(BaseModel):
    """A single row in the turn-inputs event — one assembled input the model received.

    Rows are displayed in the chat transcript as interleaved inline labeled rows,
    one per assembled input, for trajectory transparency.
    """

    label: str = Field(description="Human-readable label for the row (e.g., 'Request', 'Active skills')")
    kind: Literal["request", "skills", "history", "core_memory", "context"] = Field(
        description="Semantic kind identifying which assembled input this row represents"
    )
    value: Any = Field(description="The actual value passed to the model (str, list, or dict)")
    preview: str = Field(default="", description="Single-line preview text for collapsed display")


class RuntimeActorContext(BaseModel):
    """Who is producing this event (root agent, delegate, sub-agent)."""

    actor_kind: str | None = None
    actor_id: str | None = None
    parent_id: str | None = None
    depth: int | None = None
    max_depth: int | None = None


class RuntimeEventContext(BaseModel):
    """Stable runtime environment context attached to backend-emitted events.

    This is the single canonical definition consumed by both the runtime event
    factories and the API projection layer (``api/events/event_adapter.py``).
    """

    runtime_mode: str | None = None
    execution_mode: str | None = None
    execution_profile: str | None = None
    sandbox_id: str | None = None
    child_sandbox_id: str | None = None
    volume_name: str | None = None
    workspace_path: str | None = None
    repo_url: str | None = None
    repo_ref: str | None = None
    document_path: str | None = None
    depth: int | None = None
    max_depth: int | None = None
    actor_kind: str | None = None
    actor_id: str | None = None
    parent_id: str | None = None
    lane_key: str | None = None
    llm_call_budget: int | None = None


class RuntimeEvent(BaseModel):
    """Canonical runtime event — one structured object, no downstream re-parsing.

    Satisfies :class:`~fleet_rlm.api.runtime_services.chat_runtime.StreamEventLike`
    structurally (``kind``, ``text``, ``payload``, ``timestamp`` are all present).
    """

    kind: RuntimeEventKind
    text: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    tool: RuntimeToolInfo | None = None
    actor: RuntimeActorContext | None = None
    context: RuntimeEventContext | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: int = EVENT_SCHEMA_VERSION

    @classmethod
    def tool_call(
        cls,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        step_index: int | None = None,
        actor: RuntimeActorContext | None = None,
        context: RuntimeEventContext | None = None,
    ) -> RuntimeEvent:
        """Factory for a structured tool-call event."""
        tool_input = f"{tool_name}({tool_args})"
        return cls(
            kind=RuntimeEventKind.TOOL_CALL,
            text=f"Calling tool: {tool_input}",
            payload={
                "tool_name": tool_name,
                "tool_input": str(tool_args),
                "tool_args": tool_args,
                "step_index": step_index,
            },
            tool=RuntimeToolInfo(
                tool_name=tool_name,
                tool_args=tool_args,
                tool_input=str(tool_args),
                step_index=step_index,
            ),
            actor=actor,
            context=context,
        )

    @classmethod
    def tool_result(
        cls,
        *,
        tool_name: str,
        observation: Any,
        step_index: int | None = None,
        actor: RuntimeActorContext | None = None,
        context: RuntimeEventContext | None = None,
    ) -> RuntimeEvent:
        """Factory for a structured tool-result event."""
        return cls(
            kind=RuntimeEventKind.TOOL_RESULT,
            text=f"Tool result: {observation}",
            payload={
                "tool_name": tool_name,
                "tool_output": str(observation),
                "step_index": step_index,
            },
            tool=RuntimeToolInfo(
                tool_name=tool_name,
                tool_output=observation,
                step_index=step_index,
            ),
            actor=actor,
            context=context,
        )

    @classmethod
    def clarification(
        cls,
        *,
        message_id: str,
        question: str | None,
        step_label: str = "Clarification needed",
        options: list[Any] | None = None,
        actor: RuntimeActorContext | None = None,
    ) -> RuntimeEvent:
        """Factory for a clarification event."""
        return cls(
            kind=RuntimeEventKind.CLARIFICATION,
            text=str(question or "Please clarify your intent."),
            payload={
                "message_id": message_id,
                "question": question,
                "step_label": step_label,
                "options": options or [],
            },
            actor=actor,
        )

    @classmethod
    def status(
        cls,
        text: str,
        *,
        payload: dict[str, Any] | None = None,
        actor: RuntimeActorContext | None = None,
        context: RuntimeEventContext | None = None,
    ) -> RuntimeEvent:
        return cls(
            kind=RuntimeEventKind.STATUS,
            text=text,
            payload=payload or {},
            actor=actor,
            context=context,
        )

    @classmethod
    def reasoning(
        cls,
        text: str,
        *,
        actor: RuntimeActorContext | None = None,
    ) -> RuntimeEvent:
        return cls(
            kind=RuntimeEventKind.REASONING,
            text=text,
            payload={"phase": "reasoning"},
            actor=actor,
        )

    @classmethod
    def mlflow_span(
        cls,
        *,
        span_id: str,
        name: str,
        status: str,
        parent_span_id: str | None = None,
        trace_id: str | None = None,
        duration_ms: int | float | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        input: Any | None = None,
        output: Any | None = None,
        error: Any | None = None,
        metadata: dict[str, Any] | None = None,
        actor: RuntimeActorContext | None = None,
        context: RuntimeEventContext | None = None,
    ) -> RuntimeEvent:
        """Factory for a curated MLflow span lifecycle event."""
        normalized_status, raw_status = _normalize_mlflow_span_status(
            status,
            duration_ms=duration_ms,
            ended_at=ended_at,
            output=output,
            error=error,
        )

        span_name = name.strip() or "MLflow span"
        payload: dict[str, Any] = {
            "event_kind": "mlflow_span",
            "span_id": span_id,
            "name": span_name,
            "status": normalized_status,
            "tool_name": "mlflow_span",
        }
        if raw_status:
            payload["raw_status"] = raw_status
        if parent_span_id:
            payload["parent_span_id"] = parent_span_id
        if trace_id:
            payload["trace_id"] = trace_id
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if started_at:
            payload["started_at"] = started_at
        if ended_at:
            payload["ended_at"] = ended_at
        if input is not None:
            payload["input"] = input
        if output is not None:
            payload["output"] = output
        if error is not None:
            payload["error"] = error
        if metadata:
            payload["metadata"] = metadata

        return cls(
            kind=RuntimeEventKind.MLFLOW_SPAN,
            text=span_name,
            payload=payload,
            actor=actor,
            context=context,
        )

    @classmethod
    def turn_inputs(
        cls,
        rows: list[TurnInputRow],
        *,
        actor: RuntimeActorContext | None = None,
        context: RuntimeEventContext | None = None,
    ) -> RuntimeEvent:
        """Factory for a turn-inputs event carrying the assembled model inputs.

        Emitted once at the start of each route (RLM, ReAct, or CoT) to surface
        the inputs the model actually received as interleaved inline labeled rows
        in the chat transcript.
        """
        return cls(
            kind=RuntimeEventKind.TURN_INPUTS,
            text="Turn inputs",
            payload={"rows": [row.model_dump() for row in rows]},
            actor=actor,
            context=context,
        )


__all__ = [
    "EVENT_SCHEMA_VERSION",
    "RuntimeEvent",
    "RuntimeEventKind",
    "RuntimeToolInfo",
    "RuntimeActorContext",
    "RuntimeEventContext",
    "TurnInputRow",
]
