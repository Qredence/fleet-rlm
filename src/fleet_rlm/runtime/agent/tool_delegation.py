"""Dynamic tool delegation mixin for ReAct agent.

This module provides a mixin that replaces boilerplate delegator methods
with dynamic attribute access, reducing code duplication while maintaining
backward compatibility.

Instead of defining 25+ nearly identical methods like:

    def load_document(self, path: str, alias: str = "active") -> dict[str, Any]:
        return self._get_tool("load_document")(path, alias=alias)

We use __getattr__ to dynamically dispatch to the underlying tool.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any

import dspy

if TYPE_CHECKING:
    from .chat_agent import RLMReActChatAgent


# Frozen set of tool names that support delegation
# These names map directly to tools in the react_tools list
TOOL_DELEGATE_NAMES: frozenset[str] = frozenset(
    {
        # Document tools
        "load_document",
        "fetch_web_document",
        "set_active_document",
        "list_documents",
        # Filesystem tools
        "list_files",
        "read_file_slice",
        "find_files",
        # Chunking tools
        "chunk_host",
        "chunk_sandbox",
        # RLM delegation tools
        "parallel_semantic_map",
        "rlm_query",
        "rlm_query_batched",
        "summarize_long_document",
        "extract_from_logs",
        # Buffer tools
        "read_buffer",
        "clear_buffer",
        "save_buffer_to_volume",
        # Volume tools
        "load_text_from_volume",
        "process_document",
        "write_to_file",
        # Memory tools
        "edit_core_memory",
        "grounded_answer",
        "triage_incident_logs",
        "plan_code_change",
        "propose_core_memory_update",
        "memory_tree",
        "memory_action_intent",
        "memory_structure_audit",
        "memory_structure_migration_plan",
        "clarification_questions",
    }
)


def get_tool_by_name(agent: RLMReActChatAgent, name: str) -> Callable[..., Any]:
    """Look up a tool by name in the agent's tool list.

    Handles both raw callables (via ``__name__``) and ``dspy.Tool``
    wrappers (via ``.name``).

    Args:
        agent: The RLMReActChatAgent instance
        name: The tool name to look up

    Returns:
        The underlying callable for the tool

    Raises:
        AttributeError: If no tool with the given name exists
    """
    for tool in agent.react_tools:
        tool_name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        if tool_name == name:
            # Return the underlying callable for dspy.Tool wrappers
            fn = tool.func if isinstance(tool, dspy.Tool) else tool
            return _sync_compatible_tool_callable(fn)
    raise AttributeError(f"No tool named {name!r}")


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


