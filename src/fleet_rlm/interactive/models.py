"""Pydantic models used by the interactive coding CLI runtime."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
    stream: bool = True


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
