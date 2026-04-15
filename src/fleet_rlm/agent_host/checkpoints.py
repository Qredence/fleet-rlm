"""Checkpoint state owned by the outer Agent Framework host."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal, cast

WorkflowStage = Literal[
    "idle",
    "executing",
    "awaiting_hitl",
    "hitl_resolved",
    "retrying",
    "completed",
    "cancelled",
    "failed",
]
_WORKFLOW_STAGES = {
    "idle",
    "executing",
    "awaiting_hitl",
    "hitl_resolved",
    "retrying",
    "completed",
    "cancelled",
    "failed",
}

# Backward-compat mapping: old persisted stage names → new canonical names.
_STAGE_COMPAT: dict[str, str] = {
    "awaiting_hitl_resolution": "awaiting_hitl",
    "continued": "hitl_resolved",
}


def current_utc_iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class ContinuationCheckpoint:
    """Minimal resumable continuation metadata retained across turns."""

    continuation_token: str
    message_id: str | None = None
    source: str | None = None
    requested_at: str = ""
    updated_at: str = ""
    resolution: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ContinuationCheckpoint | None:
        continuation_token = str(payload.get("continuation_token", "")).strip()
        if not continuation_token:
            return None
        timestamp = (
            str(payload.get("updated_at", "")).strip() or current_utc_iso_timestamp()
        )
        requested_at = str(payload.get("requested_at", "")).strip() or timestamp
        return cls(
            continuation_token=continuation_token,
            message_id=str(payload.get("message_id", "")).strip() or None,
            source=str(payload.get("source", "")).strip() or None,
            requested_at=requested_at,
            updated_at=timestamp,
            resolution=str(payload.get("resolution", "")).strip() or None,
        )


@dataclass(slots=True)
class PendingApprovalCheckpoint:
    """Minimal resumable approval checkpoint owned by the outer layer."""

    message_id: str
    continuation_token: str
    workflow_stage: WorkflowStage = "awaiting_hitl"
    question: str | None = None
    source: str | None = None
    action_labels: list[str] | None = None
    requested_at: str = ""
    resolved_at: str | None = None
    resolution: str | None = None
    timeout_at: str | None = None  # ISO 8601 UTC — set when a timeout policy is active
    timed_out: bool = False  # flipped to True when timeout_at passes

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
        workflow_stage = str(payload.get("workflow_stage", "awaiting_hitl")).strip()
        # Backward-compat: map old stage names to new canonical names.
        workflow_stage = _STAGE_COMPAT.get(workflow_stage, workflow_stage)
        if workflow_stage not in _WORKFLOW_STAGES:
            workflow_stage = "awaiting_hitl"
        return cls(
            message_id=message_id,
            continuation_token=continuation_token,
            workflow_stage=cast(WorkflowStage, workflow_stage),
            question=str(payload.get("question", "")).strip() or None,
            source=str(payload.get("source", "")).strip() or None,
            action_labels=action_labels,
            requested_at=(
                str(payload.get("requested_at", "")).strip()
                or current_utc_iso_timestamp()
            ),
            resolved_at=str(payload.get("resolved_at", "")).strip() or None,
            resolution=str(payload.get("resolution", "")).strip() or None,
            timeout_at=str(payload.get("timeout_at", "")).strip() or None,
            timed_out=bool(payload.get("timed_out", False)),
        )


@dataclass(slots=True)
class CancellationCheckpoint:
    """Structured cancel-tracking record for one workflow run."""

    cancelled_at: str
    reason: str | None = None  # e.g. "user_request", "timeout", "error"
    message_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CancellationCheckpoint | None:
        if not isinstance(payload, dict):
            return None
        cancelled_at = str(payload.get("cancelled_at", "")).strip()
        if not cancelled_at:
            return None
        return cls(
            cancelled_at=cancelled_at,
            reason=str(payload.get("reason", "")).strip() or None,
            message_id=str(payload.get("message_id", "")).strip() or None,
        )


@dataclass(slots=True)
class OrchestrationCheckpointState:
    """Minimal outer orchestration continuation state persisted with the session."""

    workflow_stage: WorkflowStage = "idle"
    continuation: ContinuationCheckpoint | None = None
    pending_approval: PendingApprovalCheckpoint | None = None
    cancellation: CancellationCheckpoint | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_stage": self.workflow_stage,
            "continuation": (
                self.continuation.to_dict() if self.continuation is not None else None
            ),
            "pending_approval": (
                self.pending_approval.to_dict()
                if self.pending_approval is not None
                else None
            ),
            "cancellation": (
                self.cancellation.to_dict() if self.cancellation is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> OrchestrationCheckpointState:
        if not isinstance(payload, dict):
            return cls()
        workflow_stage = str(payload.get("workflow_stage", "idle")).strip()
        # Backward-compat: map old stage names to new canonical names.
        workflow_stage = _STAGE_COMPAT.get(workflow_stage, workflow_stage)
        if workflow_stage not in _WORKFLOW_STAGES:
            workflow_stage = "idle"
        continuation_payload = payload.get("continuation")
        continuation = (
            ContinuationCheckpoint.from_dict(continuation_payload)
            if isinstance(continuation_payload, dict)
            else None
        )
        pending_payload = payload.get("pending_approval")
        pending = (
            PendingApprovalCheckpoint.from_dict(pending_payload)
            if isinstance(pending_payload, dict)
            else None
        )
        if continuation is None and pending is not None:
            continuation = ContinuationCheckpoint(
                continuation_token=pending.continuation_token,
                message_id=pending.message_id,
                source=pending.source,
                requested_at=pending.requested_at or current_utc_iso_timestamp(),
                updated_at=(
                    pending.resolved_at
                    or pending.requested_at
                    or current_utc_iso_timestamp()
                ),
                resolution=pending.resolution,
            )
        cancellation_payload = payload.get("cancellation")
        cancellation = (
            CancellationCheckpoint.from_dict(cancellation_payload)
            if isinstance(cancellation_payload, dict)
            else None
        )
        return cls(
            workflow_stage=cast(WorkflowStage, workflow_stage),
            continuation=continuation,
            pending_approval=pending,
            cancellation=cancellation,
        )


def checkpoint_for_hitl_request(
    *,
    message_id: str,
    continuation_token: str,
    question: str | None,
    source: str | None,
    action_labels: list[str] | None,
    timeout_at: str | None = None,
) -> OrchestrationCheckpointState:
    requested_at = current_utc_iso_timestamp()
    return OrchestrationCheckpointState(
        workflow_stage="awaiting_hitl",
        continuation=ContinuationCheckpoint(
            continuation_token=continuation_token,
            message_id=message_id,
            source=source,
            requested_at=requested_at,
            updated_at=requested_at,
        ),
        pending_approval=PendingApprovalCheckpoint(
            message_id=message_id,
            continuation_token=continuation_token,
            question=question,
            source=source,
            action_labels=action_labels or [],
            requested_at=requested_at,
            timeout_at=timeout_at,
        ),
    )
