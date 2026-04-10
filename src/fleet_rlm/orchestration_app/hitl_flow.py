"""Compatibility HITL exports delegated to the Agent Framework host."""

from fleet_rlm.agent_host.hitl_flow import (
    HitlResolution,
    checkpoint_hitl_request,
    resolve_hitl_continuation as resolve_hitl_command,
)

__all__ = [
    "HitlResolution",
    "checkpoint_hitl_request",
    "resolve_hitl_command",
]
