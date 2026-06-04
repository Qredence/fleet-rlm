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
from typing import Any

from pydantic import BaseModel, Field

EVENT_SCHEMA_VERSION: int = 3


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


__all__ = [
    "EVENT_SCHEMA_VERSION",
    "RuntimeEvent",
    "RuntimeEventKind",
    "RuntimeToolInfo",
    "RuntimeActorContext",
    "RuntimeEventContext",
]
