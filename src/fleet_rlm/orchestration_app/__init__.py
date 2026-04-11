"""Transitional orchestration entrypoints around the worker boundary.

This package intentionally stays small while Agent Framework ownership moves into
``fleet_rlm.agent_host``. Re-export only the still-needed transition seams here.
"""

from .coordinator import (
    resolve_hitl_continuation,
    stream_orchestrated_workspace_task,
)
from fleet_rlm.agent_host.hitl_flow import HitlResolution
from fleet_rlm.agent_host.sessions import (
    OrchestrationSessionContext,
    SessionRecordLink,
    SessionSwitchOutcome,
    build_orchestration_session_context,
    switch_orchestration_session,
)

__all__ = [
    "HitlResolution",
    "OrchestrationSessionContext",
    "SessionRecordLink",
    "SessionSwitchOutcome",
    "build_orchestration_session_context",
    "resolve_hitl_continuation",
    "stream_orchestrated_workspace_task",
    "switch_orchestration_session",
]
