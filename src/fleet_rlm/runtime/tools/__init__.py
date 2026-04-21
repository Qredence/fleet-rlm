"""DSPy ReAct tool registry for the RLM chat agent."""

from __future__ import annotations

import asyncio
import importlib
import inspect
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterable

from ._marker import tool_fn


# ---------------------------------------------------------------------------
# Async-compatibility utility
# ---------------------------------------------------------------------------


def _sync_compatible_tool_callable(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Return *fn* with sync-call compatibility for async callables.

    - If *fn* is synchronous, return it unchanged.
    - If *fn* is async, run it via ``asyncio.run`` when no loop is running.
      When called from within a running event loop, return the coroutine so
      async callers can ``await`` it.
    """
    if not inspect.iscoroutinefunction(fn):
        return fn

    @wraps(fn)
    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(fn(*args, **kwargs))
        return fn(*args, **kwargs)

    return _wrapper


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def list_react_tool_names(tools: Iterable[Any]) -> list[str]:
    """Return stable tool names for display / debugging.

    Handles both raw callables (``__name__``) and ``dspy.Tool`` wrappers
    (``.name``).
    """
    names: list[str] = []
    for tool in tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
        names.append(name)
    return names


# ---------------------------------------------------------------------------
# Tool registry: discover_tools() and helpers
# ---------------------------------------------------------------------------


def _collect_tools_from_modules(modules: list[Any]) -> list[Any]:
    """Collect ``@tool_fn``-marked callables from a list of imported modules.

    Scans each module's namespace for callables with ``__is_fleet_tool__ =
    True``, wraps them in ``dspy.Tool`` if they are not already wrapped, and
    returns a deduplicated, alphabetically sorted list.

    This function is separated from ``discover_tools()`` to make the
    duplicate-detection logic independently testable.

    Args:
        modules: Sequence of already-imported Python module objects to scan.

    Returns:
        List of ``dspy.Tool`` instances sorted alphabetically by tool name.

    Raises:
        ValueError: If two modules define a tool function with the same name.
    """
    from dspy import Tool

    collected: dict[str, Any] = {}

    for module in modules:
        for attr_name, obj in vars(module).items():
            if attr_name.startswith("_"):
                continue
            if not callable(obj):
                continue
            if not getattr(obj, "__is_fleet_tool__", False):
                continue

            tool_name = attr_name

            if tool_name in collected:
                raise ValueError(
                    f"Duplicate tool name {tool_name!r}: already registered from "
                    f"another module. Each tool function must have a unique name."
                )

            if isinstance(obj, Tool):
                collected[tool_name] = obj
            else:
                collected[tool_name] = Tool(obj)

    return [collected[name] for name in sorted(collected)]


def discover_tools() -> list[Any]:
    """Discover all fleet tool functions from ``runtime/tools/*.py`` modules.

    Scans every Python file directly under the ``runtime/tools/`` package
    directory (excluding ``__init__.py`` and underscore-prefixed files),
    imports each module, and collects all module-level callables decorated
    with ``@tool_fn`` (i.e. those with ``__is_fleet_tool__ = True``).

    Results are returned in stable alphabetical order by tool name so that
    the list passed to ``dspy.ReAct`` is deterministic across calls.

    Returns:
        List of ``dspy.Tool`` instances ready for use with ``dspy.ReAct``.

    Raises:
        ValueError: If two tool modules define a function with the same name.
    """
    tools_dir = Path(__file__).parent
    loaded_modules: list[Any] = []

    for path in sorted(tools_dir.glob("*.py")):
        # Skip __init__.py and any underscore-prefixed files (private/marker modules)
        if path.name.startswith("_"):
            continue
        module_name = f"fleet_rlm.runtime.tools.{path.stem}"
        module = importlib.import_module(module_name)
        loaded_modules.append(module)

    return _collect_tools_from_modules(loaded_modules)


__all__ = [
    "_collect_tools_from_modules",
    "_sync_compatible_tool_callable",
    "discover_tools",
    "list_react_tool_names",
    "tool_fn",
]
