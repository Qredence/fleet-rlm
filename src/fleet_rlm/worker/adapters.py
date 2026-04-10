"""Adapters that map runtime stream payloads into worker contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .contracts import WorkspaceEvent, WorkspaceTaskRequest, WorkspaceTaskStatus

_TERMINAL_EVENT_KINDS = frozenset({"final", "cancelled", "error"})


def build_agent_stream_kwargs(request: WorkspaceTaskRequest) -> dict[str, Any]:
    """Build canonical runtime stream kwargs from a worker request."""

    return {
        "message": request.message,
        "trace": request.trace,
        "cancel_check": request.cancel_check,
        "docs_path": request.docs_path,
        "repo_url": request.repo_url,
        "repo_ref": request.repo_ref,
        "context_paths": list(request.context_paths),
        "batch_concurrency": request.batch_concurrency,
        "volume_name": request.workspace_id,
    }


def to_workspace_event(event: Any) -> WorkspaceEvent:
    """Normalize a runtime-style stream event into a worker event."""

    raw_ts = getattr(event, "timestamp", None)
    timestamp = raw_ts if isinstance(raw_ts, datetime) else datetime.now(timezone.utc)
    return WorkspaceEvent(
        kind=str(getattr(event, "kind", "status")),
        text=str(getattr(event, "text", "") or ""),
        payload=dict(getattr(event, "payload", {}) or {}),
        timestamp=timestamp,
        terminal=str(getattr(event, "kind", "")) in _TERMINAL_EVENT_KINDS,
    )


def status_from_terminal_kind(kind: str) -> WorkspaceTaskStatus:
    if kind == "final":
        return "completed"
    if kind == "cancelled":
        return "cancelled"
    return "error"
