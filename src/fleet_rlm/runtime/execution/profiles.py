"""Execution profile enum for sandbox interpreter configuration."""

from __future__ import annotations

from enum import Enum


class ExecutionProfile(str, Enum):
    """Execution profile controlling sandbox helper/tool exposure."""

    ROOT_INTERLOCUTOR = "ROOT_INTERLOCUTOR"
    RLM_ROOT = "RLM_ROOT"
    RLM_DELEGATE = "RLM_DELEGATE"
    MAINTENANCE = "MAINTENANCE"
