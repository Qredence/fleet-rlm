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
    """Read entries from the agent's durable core memory.

    Core memory stores persistent facts and context that survive across
    conversation sessions.  Entries are keyed by string identifiers and
    may contain any text value.

    When *key* is empty, all stored entries are returned.

    This standalone version raises ``RuntimeError`` because core-memory
    access requires a bound ``AgentRuntime``.  Obtain a bound tool list via
    ``AgentRuntime`` for real usage.

    Args:
        key: Memory key to look up.  Defaults to ``""`` (return all entries).

    Returns:
        Dictionary with ``status``, ``key``, and ``value`` (or ``entries``
        when returning all items).

    Raises:
        RuntimeError: When called without a bound ``AgentRuntime``.
    """
    raise RuntimeError(
        "read_core_memory requires a bound AgentRuntime with core memory initialised. "
        "Obtain a bound tool list via the agent runtime instead of calling directly."
    )


@tool_fn
def write_core_memory(key: str, value: str) -> dict[str, Any]:
    """Write or update an entry in the agent's durable core memory.

    Persists a key-value pair to core memory.  Existing values are
    overwritten.  Changes are flushed to the Daytona volume when the
    agent runtime persists its session.

    Args:
        key: Memory key to write.
        value: Text value to associate with *key*.

    Returns:
        Dictionary with ``status``, ``key``, and ``value``.

    Raises:
        RuntimeError: When called without a bound ``AgentRuntime``.
    """
    raise RuntimeError(
        "write_core_memory requires a bound AgentRuntime with core memory initialised. "
        "Obtain a bound tool list via the agent runtime instead of calling directly."
    )


__all__ = ["read_core_memory", "write_core_memory"]
