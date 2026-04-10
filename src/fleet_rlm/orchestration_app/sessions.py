"""Compatibility session exports for the shrinking orchestration_app layer."""

from fleet_rlm.agent_host.sessions import (
    OrchestrationSessionContext,
    SessionRecordLink,
    SessionSwitchOutcome,
    build_orchestration_session_context,
    switch_orchestration_session,
)

# TODO(phase-11): delete this shim once the remaining compatibility imports stop
# reaching through orchestration_app for hosted session/workflow continuation state.

__all__ = [
    "OrchestrationSessionContext",
    "SessionRecordLink",
    "SessionSwitchOutcome",
    "build_orchestration_session_context",
    "switch_orchestration_session",
]
