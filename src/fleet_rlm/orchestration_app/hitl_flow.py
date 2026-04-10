"""HITL continuation flow owned by the minimal outer orchestration layer."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from fleet_rlm.worker import WorkspaceEvent

from .checkpoints import (
    OrchestrationCheckpointState,
    checkpoint_for_hitl_request,
    utc_now_iso,
)
from .sessions import OrchestrationSessionContext


@dataclass(slots=True)
class HitlResolution:
    """Resolved HITL command output for websocket transport delivery."""

    event_payload: dict[str, Any] | None
    command_result: dict[str, Any]


def _build_hitl_resolved_event(
    *,
    message_id: str,
    action_label: str,
) -> dict[str, Any]:
    return {
        "kind": "hitl_resolved",
        "text": action_label,
        "payload": {
            "message_id": message_id,
            "resolution": action_label,
            "source": "command",
        },
        "version": 1,
        "event_id": str(uuid.uuid4()),
    }


def _build_hitl_resolution_result(
    *,
    message_id: str,
    action_label: str,
) -> dict[str, Any]:
    return {
        "status": "ok",
        "message_id": message_id,
        "resolution": action_label,
    }


def _checkpoint_payload(event: WorkspaceEvent) -> dict[str, Any]:
    payload = dict(event.payload) if isinstance(event.payload, dict) else {}
    hitl_payload = payload.get("hitl")
    if isinstance(hitl_payload, dict):
        merged = dict(hitl_payload)
        merged.update({k: v for k, v in payload.items() if k != "hitl"})
        return merged
    return payload


def _extract_action_labels(payload: dict[str, Any]) -> list[str]:
    raw_actions = payload.get("actions")
    if isinstance(raw_actions, list):
        labels = []
        for item in raw_actions:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "")).strip()
            if label:
                labels.append(label)
        if labels:
            return labels
    raw_options = payload.get("options")
    if isinstance(raw_options, list):
        return [str(item).strip() for item in raw_options if str(item).strip()]
    return []


def checkpoint_hitl_request(
    *,
    event: WorkspaceEvent,
    session: OrchestrationSessionContext | None,
) -> WorkspaceEvent:
    """Attach minimal continuation state to HITL worker events."""

    if event.kind != "hitl_request":
        return event

    payload = dict(event.payload) if isinstance(event.payload, dict) else {}
    checkpoint_payload = _checkpoint_payload(event)
    message_id = str(checkpoint_payload.get("message_id", "")).strip() or str(
        uuid.uuid4()
    )
    payload["message_id"] = message_id
    if isinstance(payload.get("hitl"), dict):
        payload["hitl"] = {**payload["hitl"], "message_id": message_id}
    if session is not None:
        session.save_checkpoint_state(
            checkpoint_for_hitl_request(
                message_id=message_id,
                continuation_token=str(uuid.uuid4()),
                question=(
                    str(checkpoint_payload.get("question", "")).strip()
                    or event.text
                    or None
                ),
                source=str(checkpoint_payload.get("source", "")).strip() or None,
                action_labels=_extract_action_labels(checkpoint_payload),
            )
        )
    return WorkspaceEvent(
        kind=event.kind,
        text=event.text,
        payload=payload,
        timestamp=event.timestamp,
        terminal=event.terminal,
    )


def finalize_hitl_state_for_terminal_event(
    *,
    event: WorkspaceEvent,
    session: OrchestrationSessionContext | None,
) -> None:
    """Mark non-HITL turns complete without discarding unresolved checkpoints."""

    if session is None or not event.terminal:
        return
    state = session.load_checkpoint_state()
    if state.pending_approval is None:
        session.save_checkpoint_state(
            OrchestrationCheckpointState(workflow_stage="completed")
        )


def resolve_hitl_command(
    *,
    command: str,
    args: dict[str, Any],
    session: OrchestrationSessionContext | None = None,
) -> HitlResolution | None:
    """Resolve HITL continuation policy in the outer orchestration layer."""

    if command != "resolve_hitl":
        return None

    message_id = str(args.get("message_id", "")).strip()
    action_label = str(args.get("action_label", "")).strip()
    if not message_id or not action_label:
        return HitlResolution(
            event_payload=None,
            command_result={
                "status": "error",
                "error": "resolve_hitl requires message_id and action_label",
                "message_id": message_id or None,
            },
        )

    if session is not None:
        state = session.load_checkpoint_state()
        pending = state.pending_approval
        if pending is not None and pending.message_id == message_id:
            pending.resolution = action_label
            pending.resolved_at = pending.resolved_at or utc_now_iso()
            session.save_checkpoint_state(
                OrchestrationCheckpointState(workflow_stage="continued")
            )

    return HitlResolution(
        event_payload=_build_hitl_resolved_event(
            message_id=message_id,
            action_label=action_label,
        ),
        command_result=_build_hitl_resolution_result(
            message_id=message_id,
            action_label=action_label,
        ),
    )
