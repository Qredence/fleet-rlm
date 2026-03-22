"""Compatibility wrapper for the historical ``fleet_rlm.runners`` module."""

from __future__ import annotations

from .cli.runners import (
    arun_react_chat_once,
    build_chat_agent_for_runtime_mode,
    build_daytona_workbench_chat_agent,
    build_react_chat_agent,
    check_secret_key,
    check_secret_presence,
    run_long_context,
    run_react_chat_once,
)
from .runtime.agent.chat_agent import RLMReActChatAgent

__all__ = [
    "RLMReActChatAgent",
    "arun_react_chat_once",
    "build_chat_agent_for_runtime_mode",
    "build_daytona_workbench_chat_agent",
    "build_react_chat_agent",
    "check_secret_key",
    "check_secret_presence",
    "run_long_context",
    "run_react_chat_once",
]
