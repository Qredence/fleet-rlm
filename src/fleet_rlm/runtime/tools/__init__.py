"""DSPy ReAct tool registry for the RLM chat agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Iterable, Literal

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
    from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent

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
            "analyze_long_document",
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
    agent: RLMReActChatAgent,
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

    from .chunking import build_chunking_tools
    from .document import build_document_tools
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


__all__ = [
    "aexecute_submit",
    "_rlm_trajectory_payload",
    "build_tool_list",
    "build_trajectory_payload",
    "chunk_text",
    "chunk_to_text",
    "execute_submit",
    "list_react_tool_names",
    "normalize_strategy",
    "resolve_document",
]
