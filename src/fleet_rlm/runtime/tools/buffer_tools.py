"""Simplified buffer and volume-operation tools for the fleet tool registry.

Exports module-level ``read_buffer``, ``write_buffer``, and ``clear_buffer``
functions marked with ``@tool_fn`` so that ``discover_tools()`` can collect
them.

Full sandbox-backed buffer operations (backed by the Daytona interpreter's
``add_buffer`` / ``read_buffer`` primitives) are provided by the builder in
``runtime/tools/sandbox/common.py`` via ``AgentRuntime``.
"""

from __future__ import annotations

from typing import Any

from fleet_rlm.runtime.tools._marker import tool_fn


@tool_fn
def read_buffer(name: str = "default") -> dict[str, Any]:
    """Read all items stored in a named sandbox buffer.

    Sandbox buffers accumulate serialised values written by ``add_buffer()``
    calls in previously executed code blocks.  Buffers persist for the
    lifetime of the Daytona workspace session.

    This standalone version raises ``RuntimeError`` because buffer access
    requires a live Daytona interpreter.  Obtain a bound tool list via
    ``AgentRuntime`` for real usage.

    Args:
        name: Name of the buffer to read.  Defaults to ``"default"``.

    Returns:
        Dictionary with ``status``, ``name``, and ``items`` list.

    Raises:
        RuntimeError: When called without a bound ``AgentRuntime`` interpreter.
    """
    raise RuntimeError(
        "read_buffer requires an active AgentRuntime with a Daytona interpreter. "
        "Obtain a bound tool list via the agent runtime instead of calling directly."
    )


@tool_fn
def write_buffer(name: str, content: str) -> dict[str, Any]:
    """Append a string value to a named sandbox buffer.

    Args:
        name: Name of the buffer to write to.
        content: String value to append to the buffer.

    Returns:
        Dictionary with ``status``, ``name``, and ``item_count``.

    Raises:
        RuntimeError: When called without a bound ``AgentRuntime`` interpreter.
    """
    raise RuntimeError(
        "write_buffer requires an active AgentRuntime with a Daytona interpreter. "
        "Obtain a bound tool list via the agent runtime instead of calling directly."
    )


@tool_fn
def clear_buffer(name: str = "default") -> dict[str, Any]:
    """Clear all items from a named sandbox buffer.

    Args:
        name: Name of the buffer to clear.  Defaults to ``"default"``.

    Returns:
        Dictionary with ``status`` and ``name``.

    Raises:
        RuntimeError: When called without a bound ``AgentRuntime`` interpreter.
    """
    raise RuntimeError(
        "clear_buffer requires an active AgentRuntime with a Daytona interpreter. "
        "Obtain a bound tool list via the agent runtime instead of calling directly."
    )


__all__ = ["clear_buffer", "read_buffer", "write_buffer"]
