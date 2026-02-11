"""Pydantic models used by the interactive coding CLI runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

TraceMode = Literal["compact", "verbose", "off"]


class ProfileConfig(BaseModel):
    """Persistent CLI profile for code-chat sessions."""

    name: str
    docs_path: str | None = None
    secret_name: str = "LITELLM"
    volume_name: str | None = None
    timeout: int = 900
    react_max_iters: int = 10
    rlm_max_iterations: int = 30
    rlm_max_llm_calls: int = 50
    trace: bool = False
    stream: bool = True


class SessionConfig(BaseModel):
    """Runtime config for an interactive session."""

    profile_name: str = "default"
    docs_path: str | None = None
    secret_name: str = "LITELLM"
    volume_name: str | None = None
    timeout: int = 900
    react_max_iters: int = 10
    rlm_max_iterations: int = 30
    rlm_max_llm_calls: int = 50
    trace: bool = False
    trace_mode: TraceMode = "compact"
    stream: bool = True
    stream_refresh_ms: int = 40


class CommandResult(BaseModel):
    """Structured command execution result for UI rendering."""

    ok: bool = True
    kind: Literal["info", "error", "assistant", "trace", "data"] = "info"
    message: str = ""
    payload: dict | list | str | None = None


class TranscriptEvent(BaseModel):
    """JSONL event persisted for a chat session transcript."""

    role: Literal["user", "assistant", "system", "trace", "status"]
    content: str = Field(default="")
    payload: dict | None = None


StreamEventKind = Literal[
    "assistant_token",
    "status",
    "reasoning_step",
    "tool_call",
    "tool_result",
    "final",
    "error",
    "cancelled",
]


@dataclass(slots=True)
class StreamEvent:
    """Streaming event emitted from agent runtime to UI surfaces."""

    kind: StreamEventKind
    text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class TurnState:
    """Accumulates stream events into a render-ready turn state."""

    assistant_tokens: list[str] = field(default_factory=list)
    transcript_text: str = ""
    reasoning_lines: list[str] = field(default_factory=list)
    tool_timeline: list[str] = field(default_factory=list)
    status_lines: list[str] = field(default_factory=list)
    stream_chunks: list[str] = field(default_factory=list)
    thought_chunks: list[str] = field(default_factory=list)
    status_messages: list[str] = field(default_factory=list)
    trajectory: dict[str, Any] = field(default_factory=dict)
    final_text: str = ""
    history_turns: int = 0
    token_count: int = 0
    cancelled: bool = False
    errored: bool = False
    done: bool = False
    error_message: str = ""

    def apply(self, event: StreamEvent) -> None:
        """Apply one event to state in a deterministic way."""
        if event.kind == "assistant_token":
            token = event.text
            self.assistant_tokens.append(token)
            self.stream_chunks.append(token)
            self.token_count += 1
            self.transcript_text = "".join(self.assistant_tokens)
            return

        if event.kind == "status":
            if event.text:
                self.status_lines.append(event.text)
                self.status_messages.append(event.text)
                self.reasoning_lines.append(event.text)
            return

        if event.kind == "reasoning_step":
            if event.text:
                self.reasoning_lines.append(event.text)
                self.thought_chunks.append(event.text)
            return

        if event.kind == "tool_call":
            if event.text:
                self.tool_timeline.append(event.text)
            return

        if event.kind == "tool_result":
            if event.text:
                self.tool_timeline.append(event.text)
            return

        if event.kind == "final":
            final_text = event.text or self.transcript_text
            self.final_text = final_text
            self.transcript_text = final_text
            self.trajectory = dict(event.payload.get("trajectory", {}) or {})
            self.history_turns = int(event.payload.get("history_turns", self.history_turns))
            self.done = True
            return

        if event.kind == "cancelled":
            self.cancelled = True
            self.done = True
            cancelled_text = event.text or self.transcript_text
            self.final_text = cancelled_text
            self.transcript_text = cancelled_text
            self.history_turns = int(event.payload.get("history_turns", self.history_turns))
            return

        if event.kind == "error":
            self.errored = True
            self.done = True
            self.error_message = event.text or "unknown error"
            self.history_turns = int(event.payload.get("history_turns", self.history_turns))
