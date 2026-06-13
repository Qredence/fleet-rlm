"""Event dataclasses and serialization for WebSocket streaming."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from fleet_rlm.api.events.project_chat import project_chat
from fleet_rlm.runtime.events import RuntimeEvent


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
    event: RuntimeEvent,
    payload: Any = None,
    sequence: int = 0,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Serialize one runtime stream event for websocket delivery."""
    payload_override = payload if isinstance(payload, dict) and payload is not event.payload else None
    return project_chat(
        event,
        sequence=sequence,
        run_id=run_id,
        payload_override=payload_override,
    )


def _is_terminal_transport_event(event: RuntimeEvent) -> bool:
    """Return websocket-terminal semantics for one runtime event."""
    return event.kind.is_terminal()


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


async def stream_agent_turn(
    request: WorkspaceTaskRequest,
) -> AsyncIterator[RuntimeEvent]:
    """Stream one workspace task directly through the agent without HITL wrapper."""
    if request.execution_mode is not None:
        request.agent.set_execution_mode(request.execution_mode)
    if request.prepare is not None:
        await request.prepare()
    async for runtime_event in request.agent.aiter_chat_turn_stream(**_build_agent_stream_kwargs(request)):
        yield runtime_event


__all__ = [
    "WorkspaceTaskRequest",
    "build_stream_event_dict",
    "_is_terminal_transport_event",
    "_build_agent_stream_kwargs",
    "stream_agent_turn",
]
