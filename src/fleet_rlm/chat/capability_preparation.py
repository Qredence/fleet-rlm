"""Compatibility re-exports for capability preparation."""

from __future__ import annotations

from fleet_rlm.chat.preparation import (
    EmptySkillHost,
    PreparedHostCapabilities,
    prepare_host_capabilities,
    skill_event,
)

__all__ = [
    "EmptySkillHost",
    "PreparedHostCapabilities",
    "prepare_host_capabilities",
    "skill_event",
]
