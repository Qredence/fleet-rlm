"""Shared runtime typing helpers for CLI and API layers.

Streaming event DTOs live in :mod:`fleet_rlm.runtime.events`.
"""

from __future__ import annotations

from typing import Literal

TraceMode = Literal["compact", "verbose", "off"]

__all__ = ["TraceMode"]
