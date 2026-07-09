"""Explicit DSPy ReAct tool registry for fleet runtime tools."""

from __future__ import annotations

import functools
import importlib
from typing import Any, Iterable

from fleet_rlm.tools.registry import ToolExposurePolicy, filter_tool_names

from ._marker import tool_fn

TOOL_MODULE_NAMES: tuple[str, ...] = (
    "artifacts",
    "browser_tools",
    "chunking_tools",
    "document_tools",
    "filesystem",
    "knowledge_tools",
    "rlm_delegate",
    "sandbox_filesystem",
    "sandbox_tools",
    "skill_tools",
    "volume_memory_tools",
    "web_tools",
)


def list_react_tool_names(tools: Iterable[Any]) -> list[str]:
    """Return stable tool names for display and debugging."""
    names: list[str] = []
    for tool in tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
        names.append(name)
    return names


def _collect_tools_from_modules(modules: list[Any]) -> list[Any]:
    """Collect ``@tool_fn``-marked callables from imported modules."""
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
                    "another module. Each tool function must have a unique name."
                )

            if isinstance(obj, Tool):
                collected[tool_name] = obj
            else:
                collected[tool_name] = Tool(obj)

    return [collected[name] for name in sorted(collected)]


def _import_tool_modules(
    module_names: tuple[str, ...] = TOOL_MODULE_NAMES,
) -> list[Any]:
    """Import explicitly registered runtime tool modules."""
    return [importlib.import_module(f"fleet_rlm.runtime.tools.{module_name}") for module_name in module_names]


@functools.lru_cache(maxsize=1)
def _discover_unfiltered_tools() -> tuple[Any, ...]:
    """Discover all explicitly registered fleet tool functions before policy."""
    return tuple(_collect_tools_from_modules(_import_tool_modules()))


def discover_tools(
    *,
    tool_policy: ToolExposurePolicy | None = None,
    sandbox_available: bool = False,
) -> list[Any]:
    """Discover all explicitly registered fleet tool functions.

    The result is cached because tool modules are static after import.
    """
    tools = list(_discover_unfiltered_tools())
    tool_by_name = {name: tool for name, tool in zip(list_react_tool_names(tools), tools, strict=True)}
    filtered_names = filter_tool_names(
        list(tool_by_name),
        policy=tool_policy,
        sandbox_available=sandbox_available,
    )
    return [tool_by_name[name] for name in filtered_names]


setattr(discover_tools, "cache_clear", _discover_unfiltered_tools.cache_clear)


__all__ = [
    "TOOL_MODULE_NAMES",
    "_collect_tools_from_modules",
    "_import_tool_modules",
    "discover_tools",
    "list_react_tool_names",
    "tool_fn",
]
