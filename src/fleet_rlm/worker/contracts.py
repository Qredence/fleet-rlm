"""Transport-agnostic contracts for running a single workspace task."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

WorkspaceTaskStatus = Literal["completed", "cancelled", "error"]


class WorkspaceTaskAgent(Protocol):
    """Minimal runtime agent surface required by the worker boundary."""

    def set_execution_mode(self, execution_mode: str) -> None: ...

    def aiter_chat_turn_stream(
        self,
        message: str,
        trace: bool = True,
        cancel_check: Callable[[], bool] | None = None,
        *,
        docs_path: str | None = None,
        repo_url: str | None = None,
        repo_ref: str | None = None,
        context_paths: list[str] | None = None,
        batch_concurrency: int | None = None,
        volume_name: str | None = None,
    ) -> AsyncIterator[object]: ...


@dataclass(slots=True)
class WorkspaceTaskRequest:
    """Input needed to execute one workspace task end-to-end."""

    agent: WorkspaceTaskAgent
    message: str
    execution_mode: str | None = None
    trace: bool = True
    docs_path: str | None = None
    repo_url: str | None = None
    repo_ref: str | None = None
    context_paths: list[str] | None = None
    batch_concurrency: int | None = None
    workspace_id: str | None = None
    cancel_check: Callable[[], bool] | None = None
    prepare: Callable[[], Awaitable[None]] | None = None


@dataclass(slots=True)
class WorkspaceEvent:
    """Normalized worker event shape for streaming and collection."""

    kind: str
    text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    terminal: bool = False


@dataclass(slots=True)
class WorkspaceTaskResult:
    """Collected result for one completed workspace task."""

    status: WorkspaceTaskStatus
    terminal_event: WorkspaceEvent
    output_text: str
    payload: dict[str, Any]
    events: list[WorkspaceEvent] = field(default_factory=list)
