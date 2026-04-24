"""Compatibility re-export for the canonical runtime factory."""

from __future__ import annotations

from fleet_rlm.runtime.factory import (
    _require_planner_ready,
    build_chat_agent,
)

__all__ = [
    "_require_planner_ready",
    "build_chat_agent",
]
