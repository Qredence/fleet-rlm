"""DSPy ReAct tool registry for the RLM chat agent."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal

from ._marker import tool_fn
from .shared import (
    aexecute_submit,
    _rlm_trajectory_payload,
    build_trajectory_payload,
    chunk_text,
    chunk_to_text,
    execute_submit,
    normalize_strategy,
    resolve_document,
)

if TYPE_CHECKING:
    from fleet_rlm.runtime.agent.runtime import _LegacyAgentRuntime as AgentRuntime

ExecutionMode = Literal["auto", "rlm_only", "tools_only"]

_RECURSIVE_RLM_TOOL_NAMES: frozenset[str] = frozenset(
    {"rlm_query", "rlm_query_batched"}
)

_SANDBOX_BATCH_RLM_TOOL_NAMES: frozenset[str] = frozenset({"parallel_semantic_map"})

_MEMORY_INTELLIGENCE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "memory_tree",
        "memory_action_intent",
        "memory_structure_audit",
        "memory_structure_migration_plan",
        "clarification_questions",
    }
)

_CACHED_RUNTIME_MODULE_TOOL_NAMES: frozenset[str] = (
    frozenset(
        {
            "summarize_long_document",
            "extract_from_logs",
            "grounded_answer",
            "triage_incident_logs",
            "plan_code_change",
            "propose_core_memory_update",
        }
    )
    | _MEMORY_INTELLIGENCE_TOOL_NAMES
)

_RLM_HEAVY_TOOL_NAMES: frozenset[str] = (
    _RECURSIVE_RLM_TOOL_NAMES
    | _CACHED_RUNTIME_MODULE_TOOL_NAMES
    | _SANDBOX_BATCH_RLM_TOOL_NAMES
)

_RLM_ONLY_TOOL_NAMES: frozenset[str] = _RECURSIVE_RLM_TOOL_NAMES


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_tool_list(
    agent: AgentRuntime,
    extra_tools: list[Callable[..., Any]] | None = None,
) -> list[Any]:
    """Build the DSPy ReAct tool list with closures bound to *agent*.

    Each inner function has a descriptive ``__name__``, docstring, and
    type-hinted parameters so ``dspy.ReAct`` can introspect them cleanly.

    Tools are organized by category and imported from dedicated modules:
    - Document tools: load_document, set_active_document, list_documents
    - Filesystem tools: list_files, read_file_slice, find_files
    - Chunking tools: chunk_host, chunk_sandbox
    - Sandbox tools: RLM delegation, memory, buffer, volume operations
    """
    from dspy import Tool

    from .content import build_chunking_tools, build_document_tools
    from .filesystem import build_filesystem_tools
    from .sandbox import build_sandbox_tools

    tools: list[Tool] = []

    # Document management tools (load, set_active, list)
    tools.extend(build_document_tools(agent))

    # Filesystem navigation tools (list, read_slice, find)
    tools.extend(build_filesystem_tools(agent))

    # Chunking tools (host and sandbox)
    tools.extend(build_chunking_tools(agent))

    # Sandbox / RLM / buffer / volume tools
    tools.extend(build_sandbox_tools(agent))

    # Wrap extra tools with dspy.Tool if not already wrapped
    if extra_tools:
        for et in extra_tools:
            if isinstance(et, Tool):
                tools.append(et)
            else:
                tools.append(Tool(et))

    return _filter_tools_for_execution_mode(
        tools,
        getattr(agent, "execution_mode", "auto"),
    )


def _filter_tools_for_execution_mode(
    tools: list[Any], execution_mode: ExecutionMode | str
) -> list[Any]:
    """Return the subset of *tools* allowed for the selected execution mode."""
    if execution_mode == "tools_only":
        return [tool for tool in tools if _tool_name(tool) not in _RLM_HEAVY_TOOL_NAMES]

    # In ``rlm_only`` mode we intentionally restrict the agent to the explicit
    # true-recursion tool allowlist (currently just ``rlm_query`` via
    # ``_RLM_ONLY_TOOL_NAMES``). Cached runtime-module tools stay disabled here
    # so the execution mode remains an unambiguous recursive-RLM path.
    if execution_mode == "rlm_only":
        return [tool for tool in tools if _tool_name(tool) in _RLM_ONLY_TOOL_NAMES]

    return tools


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", None) or getattr(tool, "__name__", ""))


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
    "aexecute_submit",
    "_collect_tools_from_modules",
    "_rlm_trajectory_payload",
    "build_tool_list",
    "build_trajectory_payload",
    "chunk_text",
    "chunk_to_text",
    "discover_tools",
    "execute_submit",
    "list_react_tool_names",
    "normalize_strategy",
    "resolve_document",
    "tool_fn",
]
