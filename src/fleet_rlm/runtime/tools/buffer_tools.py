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
    """Read the contents of a named Daytona sandbox buffer."""
    raise RuntimeError(
        "read_buffer requires an active AgentRuntime with a Daytona interpreter. "
        "Obtain a bound tool list via the agent runtime instead of calling directly."
    )


@tool_fn
def write_buffer(name: str, content: str) -> dict[str, Any]:
    """Append text to a named Daytona sandbox buffer."""
    raise RuntimeError(
        "write_buffer requires an active AgentRuntime with a Daytona interpreter. "
        "Obtain a bound tool list via the agent runtime instead of calling directly."
    )


@tool_fn
def clear_buffer(name: str = "default") -> dict[str, Any]:
    """Clear all entries from a named Daytona sandbox buffer."""
    raise RuntimeError(
        "clear_buffer requires an active AgentRuntime with a Daytona interpreter. "
        "Obtain a bound tool list via the agent runtime instead of calling directly."
    )


__all__ = ["clear_buffer", "read_buffer", "write_buffer"]
