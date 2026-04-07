"""Compatibility re-export for the canonical runtime factory."""

from __future__ import annotations

from fleet_rlm.runtime.factory import (
    _ReActAgentOptions,
    _build_react_agent_from_options,
    _require_planner_ready,
    build_chat_agent,
)

__all__ = [
    "_ReActAgentOptions",
    "_build_react_agent_from_options",
    "_require_planner_ready",
    "build_chat_agent",
]
