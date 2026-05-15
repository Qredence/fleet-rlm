"""Simplified core-memory tools for the fleet tool registry.

Exports module-level ``read_core_memory`` and ``write_core_memory`` functions
marked with ``@tool_fn`` so that ``discover_tools()`` can collect them.

Core memory is a durable key-value store persisted to the Daytona volume
under ``memory/core/``.  The full agent-bound implementations live in
``runtime/tools/sandbox/memory.py`` (memory intelligence) and the agent
runtime's ``core_memory`` dict attribute.
"""

from __future__ import annotations

from typing import Any

from fleet_rlm.runtime.tools._marker import tool_fn


@tool_fn
def read_core_memory(key: str = "") -> dict[str, Any]:
    """Read agent core-memory entries by key or return all entries."""
    raise RuntimeError(
        "read_core_memory requires a bound AgentRuntime with core memory initialised. "
        "Obtain a bound tool list via the agent runtime instead of calling directly."
    )


@tool_fn
def write_core_memory(key: str, value: str) -> dict[str, Any]:
    """Write a text value into agent core memory."""
    raise RuntimeError(
        "write_core_memory requires a bound AgentRuntime with core memory initialised. "
        "Obtain a bound tool list via the agent runtime instead of calling directly."
    )


__all__ = ["read_core_memory", "write_core_memory"]
