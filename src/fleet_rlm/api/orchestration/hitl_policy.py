"""HITL command-resolution policy isolated from websocket transport."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


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


def resolve_hitl_command(
    *,
    command: str,
    args: dict[str, Any],
) -> HitlResolution | None:
    """Resolve HITL continuation policy for the special websocket command."""

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
