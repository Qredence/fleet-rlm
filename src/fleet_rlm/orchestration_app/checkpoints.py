"""Minimal checkpoint state for outer orchestration ownership."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal, cast

WorkflowStage = Literal[
    "idle",
    "awaiting_hitl_resolution",
    "continued",
    "completed",
]
_WORKFLOW_STAGES = {
    "idle",
    "awaiting_hitl_resolution",
    "continued",
    "completed",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class PendingApprovalCheckpoint:
    """Minimal resumable approval checkpoint owned by the outer layer."""

    message_id: str
    continuation_token: str
    workflow_stage: WorkflowStage = "awaiting_hitl_resolution"
    question: str | None = None
    source: str | None = None
    action_labels: list[str] | None = None
    requested_at: str = ""
    resolved_at: str | None = None
    resolution: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["action_labels"] = list(self.action_labels or [])
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PendingApprovalCheckpoint | None:
        message_id = str(payload.get("message_id", "")).strip()
        continuation_token = str(payload.get("continuation_token", "")).strip()
        if not message_id or not continuation_token:
            return None
        actions = payload.get("action_labels")
        action_labels = (
            [str(item).strip() for item in actions if str(item).strip()]
            if isinstance(actions, list)
            else []
        )
        workflow_stage = str(
            payload.get("workflow_stage", "awaiting_hitl_resolution")
        ).strip()
        if workflow_stage not in _WORKFLOW_STAGES:
            workflow_stage = "awaiting_hitl_resolution"
        return cls(
            message_id=message_id,
            continuation_token=continuation_token,
            workflow_stage=cast(WorkflowStage, workflow_stage),
            question=str(payload.get("question", "")).strip() or None,
            source=str(payload.get("source", "")).strip() or None,
            action_labels=action_labels,
            requested_at=str(payload.get("requested_at", "")).strip() or utc_now_iso(),
            resolved_at=str(payload.get("resolved_at", "")).strip() or None,
            resolution=str(payload.get("resolution", "")).strip() or None,
        )


@dataclass(slots=True)
class OrchestrationCheckpointState:
    """Minimal outer orchestration continuation state persisted with the session."""

    workflow_stage: WorkflowStage = "idle"
    pending_approval: PendingApprovalCheckpoint | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_stage": self.workflow_stage,
            "pending_approval": (
                self.pending_approval.to_dict()
                if self.pending_approval is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> OrchestrationCheckpointState:
        if not isinstance(payload, dict):
            return cls()
        workflow_stage = str(payload.get("workflow_stage", "idle")).strip()
        if workflow_stage not in _WORKFLOW_STAGES:
            workflow_stage = "idle"
        pending_payload = payload.get("pending_approval")
        pending = (
            PendingApprovalCheckpoint.from_dict(pending_payload)
            if isinstance(pending_payload, dict)
            else None
        )
        return cls(
            workflow_stage=cast(WorkflowStage, workflow_stage),
            pending_approval=pending,
        )


def checkpoint_for_hitl_request(
    *,
    message_id: str,
    continuation_token: str,
    question: str | None,
    source: str | None,
    action_labels: list[str] | None,
) -> OrchestrationCheckpointState:
    return OrchestrationCheckpointState(
        workflow_stage="awaiting_hitl_resolution",
        pending_approval=PendingApprovalCheckpoint(
            message_id=message_id,
            continuation_token=continuation_token,
            question=question,
            source=source,
            action_labels=action_labels or [],
            requested_at=utc_now_iso(),
        ),
    )
