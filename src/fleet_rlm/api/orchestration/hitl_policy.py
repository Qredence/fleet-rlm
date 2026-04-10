"""Compatibility HITL policy delegating into the outer orchestration layer."""

from __future__ import annotations

from typing import Any

from fleet_rlm.orchestration_app import (
    HitlResolution,
    OrchestrationSessionContext,
    resolve_hitl_continuation,
)


def resolve_hitl_command(
    *,
    command: str,
    args: dict[str, Any],
    session_record: dict[str, Any] | None = None,
) -> HitlResolution | None:
    """Resolve the special websocket HITL continuation command via outer glue."""

    return resolve_hitl_continuation(
        command=command,
        args=args,
        session=OrchestrationSessionContext.from_session_record(session_record),
    )
