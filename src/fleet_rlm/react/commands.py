"""Command dispatch for the RLM ReAct chat agent.

Provides the :data:`COMMAND_DISPATCH` table mapping command names to tool
functions, and :func:`execute_command` which resolves and invokes them.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import RLMReActChatAgent

# ---------------------------------------------------------------------------
# Dispatch table: command_name -> (tool_function_name, required_args, optional_args)
# ---------------------------------------------------------------------------

COMMAND_DISPATCH: dict[str, tuple[str, list[str], list[str]]] = {
    "load_document": ("load_document", ["path"], ["alias"]),
    "set_active_document": ("set_active_document", ["alias"], []),
    "list_documents": ("list_documents", [], []),
    "list_files": ("list_files", [], ["path", "pattern"]),
    "read_file_slice": ("read_file_slice", ["path"], ["start_line", "num_lines"]),
    "find_files": ("find_files", ["pattern"], ["path", "include"]),
    "chunk_host": (
        "chunk_host",
        ["strategy"],
        ["alias", "size", "overlap", "pattern"],
    ),
    "chunk_sandbox": (
        "chunk_sandbox",
        ["strategy"],
        ["variable_name", "buffer_name", "size", "overlap", "pattern"],
    ),
    "parallel_semantic_map": (
        "parallel_semantic_map",
        ["query"],
        ["chunk_strategy", "max_chunks", "buffer_name"],
    ),
    "analyze_document": (
        "analyze_long_document",
        ["query"],
        ["alias", "include_trajectory"],
    ),
    "summarize_document": (
        "summarize_long_document",
        ["focus"],
        ["alias", "include_trajectory"],
    ),
    "extract_logs": (
        "extract_from_logs",
        ["query"],
        ["alias", "include_trajectory"],
    ),
    "read_buffer": ("read_buffer", ["name"], []),
    "clear_buffer": ("clear_buffer", [], ["name"]),
    "save_buffer": ("save_buffer_to_volume", ["name", "path"], []),
    "load_volume": ("load_text_from_volume", ["path"], ["alias"]),
    "reset": ("reset", [], ["clear_sandbox_buffers"]),
}

# Commands whose tools may block the event loop and should be offloaded.
_BLOCKING_COMMANDS = frozenset(
    {
        "analyze_document",
        "summarize_document",
        "extract_logs",
        "parallel_semantic_map",
        "chunk_sandbox",
        "save_buffer",
        "load_volume",
        "read_buffer",
        "find_files",
    }
)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


async def execute_command(
    agent: RLMReActChatAgent,
    command: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch *command* to the corresponding agent tool.

    Long-running / blocking tools are offloaded to a thread via
    :func:`asyncio.to_thread` so they don't block the event loop.

    The function first looks for the tool in the agent's ``react_tools``
    list (closure-based tools built by :func:`build_tool_list`), falling
    back to a direct method lookup on the agent for ``reset``.
    """
    if command not in COMMAND_DISPATCH:
        raise ValueError(
            f"Unknown command: {command}. "
            f"Available: {', '.join(sorted(COMMAND_DISPATCH))}"
        )

    tool_name, required, optional = COMMAND_DISPATCH[command]
    missing = [k for k in required if k not in args]
    if missing:
        raise ValueError(f"Missing required args for {command}: {', '.join(missing)}")

    # Build kwargs from provided args, filtering to known params
    all_known = set(required) | set(optional)
    kwargs = {k: v for k, v in args.items() if k in all_known}

    # Resolve the callable: look up by __name__ in the react_tools list,
    # falling back to agent method for commands like "reset".
    tool_fn = _resolve_tool(agent, tool_name)

    if command in _BLOCKING_COMMANDS:
        return await asyncio.to_thread(tool_fn, **kwargs)

    return tool_fn(**kwargs)


def _resolve_tool(agent: RLMReActChatAgent, tool_name: str) -> Any:
    """Find a tool by name in the agent's tool list or as a method."""
    for tool in agent.react_tools:
        if getattr(tool, "__name__", None) == tool_name:
            return tool
    # Fallback: direct method (e.g. reset)
    return getattr(agent, tool_name)
