"""Fleet tool marker decorator for the discover_tools() registry scan."""

from __future__ import annotations

from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def tool_fn(func: F) -> F:
    """Mark a module-level callable as a fleet tool for discover_tools() discovery.

    Functions decorated with @tool_fn are collected by discover_tools() and
    wrapped in dspy.Tool for registration with dspy.ReAct.  Helper functions
    and builder factories that are not decorated are ignored by the scan.

    Usage::

        from fleet_rlm.runtime.tools._marker import tool_fn

        @tool_fn
        def my_tool(query: str) -> dict[str, Any]:
            \"\"\"Perform some operation.\"\"\"
            ...

    Args:
        func: The callable to mark as a fleet tool.

    Returns:
        The callable, unchanged except for the ``__is_fleet_tool__`` attribute.
    """
    func.__is_fleet_tool__ = True  # type: ignore[attr-defined]
    return func


__all__ = ["tool_fn"]
