"""Structured turn recording (safe identifiers only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID


@dataclass
class TurnTrace:
    """In-memory record of one turn for operators / optional exporters."""

    run_id: UUID
    session_id: UUID
    user_id: UUID
    workspace_id: UUID
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    terminal_status: str | None = None
    duration_ms: int | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    model_profiles: dict[str, str] = field(default_factory=dict)
    skill_ids: list[str] = field(default_factory=list)
    attachment_ids: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    sandbox_id: str | None = None
    volume_id: str | None = None
    mount_path: str | None = None
    error_message: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "session_id": str(self.session_id),
            "user_id": str(self.user_id),
            "workspace_id": str(self.workspace_id),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "terminal_status": self.terminal_status,
            "duration_ms": self.duration_ms,
            "usage": dict(self.usage),
            "model_profiles": dict(self.model_profiles),
            "skill_ids": list(self.skill_ids),
            "attachment_ids": list(self.attachment_ids),
            "artifact_ids": list(self.artifact_ids),
            "sandbox_id": self.sandbox_id,
            "volume_id": self.volume_id,
            "mount_path": self.mount_path,
            "error_message": self.error_message,
        }


def apply_event_to_trace(trace: TurnTrace, kind: str, payload: dict[str, Any]) -> None:
    """Mutate trace from a public RuntimeEvent kind + payload."""
    if kind in {"skill.activated", "skill.loaded"}:
        sid = str(payload.get("skill_id") or "")
        if sid and sid not in trace.skill_ids:
            trace.skill_ids.append(sid)
    elif kind == "attachment.read":
        aid = str(payload.get("attachment_id") or "")
        if aid and aid not in trace.attachment_ids:
            trace.attachment_ids.append(aid)
    elif kind == "artifact.created":
        aid = str(payload.get("artifact_id") or "")
        if aid and aid not in trace.artifact_ids:
            trace.artifact_ids.append(aid)
    elif kind == "usage":
        usage = payload.get("usage")
        if isinstance(usage, dict):
            trace.usage.update(usage)
    elif kind in {"run.completed", "error"}:
        trace.terminal_status = str(payload.get("status") or kind)
        if payload.get("duration_ms") is not None:
            try:
                trace.duration_ms = int(payload["duration_ms"])
            except (TypeError, ValueError):
                pass
        if kind == "error":
            msg = payload.get("message")
            if isinstance(msg, str):
                trace.error_message = msg
        trace.finished_at = datetime.now(UTC)
