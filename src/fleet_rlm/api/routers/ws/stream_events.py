"""Event dataclasses and serialization for WebSocket streaming."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fleet_rlm.api.events.event_adapter import adapt_stream_event, build_chat_event_payload, is_terminal_backend_event
from fleet_rlm.api.events.project_chat import project_chat
from fleet_rlm.runtime.events import RuntimeEvent
from fleet_rlm.runtime.execution.streaming_events import is_terminal_stream_event_kind

from ...runtime_services.chat_runtime import StreamEventLike


@dataclass(slots=True)
class WorkspaceEvent:
    """Normalized event shape for websocket streaming."""

    kind: str
    text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    terminal: bool = False


@dataclass(slots=True)
class WorkspaceTaskRequest:
    """Input needed to execute one workspace task end-to-end."""

    agent: Any
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
    prepare: Callable[[], Any] | None = None


def build_stream_event_dict(
    *,
    event: StreamEventLike,
    payload: Any,
    sequence: int = 0,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Serialize one stream event for websocket delivery.

    Uses the typed :func:`~fleet_rlm.api.events.project_chat.project_chat`
    projector when *event* is a :class:`~fleet_rlm.runtime.events.RuntimeEvent`,
    falling back to the legacy ``adapt_stream_event`` path for plain
    ``WorkspaceEvent`` / ``StreamEventLike`` objects.
    """
    if isinstance(event, RuntimeEvent):
        return project_chat(event, sequence=sequence, run_id=run_id)
    backend_event = adapt_stream_event(
        kind=event.kind,
        text=event.text,
        payload=payload if isinstance(payload, dict) else None,
        timestamp=event.timestamp,
    )
    event_dict = build_chat_event_payload(backend_event)
    event_dict.setdefault("event_id", uuid.uuid4().hex)
    return event_dict


def _is_terminal_transport_event(event: StreamEventLike) -> bool:
    """Return websocket-terminal semantics for worker and runtime events."""
    if isinstance(event, RuntimeEvent):
        return event.kind.is_terminal()
    return bool(getattr(event, "terminal", False)) or is_terminal_backend_event(
        adapt_stream_event(
            kind=event.kind,
            text=event.text,
            payload=event.payload if isinstance(event.payload, dict) else None,
            timestamp=event.timestamp,
        )
    )


def _build_agent_stream_kwargs(request: WorkspaceTaskRequest) -> dict[str, Any]:
    """Build canonical runtime stream kwargs from a workspace task request."""
    kwargs: dict[str, Any] = {
        "message": request.message,
        "trace": request.trace,
        "cancel_check": request.cancel_check,
        "docs_path": request.docs_path,
    }
    if request.repo_url is not None:
        kwargs["repo_url"] = request.repo_url
    if request.repo_ref is not None:
        kwargs["repo_ref"] = request.repo_ref
    if request.context_paths is not None:
        kwargs["context_paths"] = list(request.context_paths)
    if request.batch_concurrency is not None:
        kwargs["batch_concurrency"] = request.batch_concurrency
    if request.workspace_id is not None:
        kwargs["volume_name"] = request.workspace_id
    return kwargs


def _to_workspace_event(event: Any) -> WorkspaceEvent:
    """Normalize a runtime-style stream event into a workspace event."""
    raw_ts = getattr(event, "timestamp", None)
    timestamp = raw_ts if isinstance(raw_ts, datetime) else datetime.now(timezone.utc)

    kind = getattr(event, "kind", "status")
    if hasattr(kind, "value"):
        kind = getattr(kind, "value")
    kind = str(kind)

    return WorkspaceEvent(
        kind=kind,
        text=str(getattr(event, "text", "") or ""),
        payload=dict(getattr(event, "payload", {}) or {}),
        timestamp=timestamp,
        terminal=is_terminal_stream_event_kind(kind),
    )


async def stream_agent_turn(
    request: WorkspaceTaskRequest,
) -> AsyncIterator[WorkspaceEvent]:
    """Stream one workspace task directly through the agent without HITL wrapper."""
    if request.execution_mode is not None:
        request.agent.set_execution_mode(request.execution_mode)
    if request.prepare is not None:
        await request.prepare()
    async for runtime_event in request.agent.aiter_chat_turn_stream(**_build_agent_stream_kwargs(request)):
        yield _to_workspace_event(runtime_event)


__all__ = [
    "WorkspaceEvent",
    "WorkspaceTaskRequest",
    "build_stream_event_dict",
    "_is_terminal_transport_event",
    "_build_agent_stream_kwargs",
    "_to_workspace_event",
    "stream_agent_turn",
]
