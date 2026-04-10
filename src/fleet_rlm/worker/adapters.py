"""Adapters that map runtime stream payloads into worker contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fleet_rlm.runtime.execution.streaming import is_terminal_stream_event_kind

from .contracts import WorkspaceEvent, WorkspaceTaskRequest, WorkspaceTaskStatus


def build_agent_stream_kwargs(request: WorkspaceTaskRequest) -> dict[str, Any]:
    """Build canonical runtime stream kwargs from a worker request."""

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


def to_workspace_event(event: Any) -> WorkspaceEvent:
    """Normalize a runtime-style stream event into a worker event."""

    raw_ts = getattr(event, "timestamp", None)
    timestamp = raw_ts if isinstance(raw_ts, datetime) else datetime.now(timezone.utc)
    return WorkspaceEvent(
        kind=str(getattr(event, "kind", "status")),
        text=str(getattr(event, "text", "") or ""),
        payload=dict(getattr(event, "payload", {}) or {}),
        timestamp=timestamp,
        terminal=is_terminal_stream_event_kind(str(getattr(event, "kind", ""))),
    )


def status_from_terminal_kind(kind: str) -> WorkspaceTaskStatus:
    if kind == "final":
        return "completed"
    if kind == "cancelled":
        return "cancelled"
    return "error"
