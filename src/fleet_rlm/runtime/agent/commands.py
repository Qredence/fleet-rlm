"""Command dispatch for the RLM ReAct chat agent.

Provides the :data:`COMMAND_DISPATCH` table mapping command names to tool
functions, and :func:`execute_command` which resolves and invokes them.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .chat_agent import RLMReActChatAgent

# ---------------------------------------------------------------------------
# Dispatch table: command_name -> (tool_function_name, required_args, optional_args)
# ---------------------------------------------------------------------------

COMMAND_DISPATCH: dict[str, tuple[str, list[str], list[str]]] = {
    "load_document": ("load_document", ["path"], ["alias"]),
    "fetch_web_document": ("fetch_web_document", ["url"], ["alias"]),
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
    "rlm_query": ("rlm_query", ["query"], ["context"]),
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
    "grounded_answer": (
        "grounded_answer",
        ["query"],
        ["alias", "chunk_strategy", "max_chunks", "include_trajectory"],
    ),
    "triage_logs": (
        "triage_incident_logs",
        ["query"],
        ["alias", "service_context", "include_trajectory"],
    ),
    "plan_code_change": (
        "plan_code_change",
        ["task"],
        ["repo_context", "constraints", "include_trajectory"],
    ),
    "memory_tree": (
        "memory_tree",
        [],
        ["root_path", "max_depth", "include_hidden"],
    ),
    "memory_action_intent": (
        "memory_action_intent",
        ["user_request"],
        ["policy_constraints"],
    ),
    "memory_structure_audit": (
        "memory_structure_audit",
        [],
        ["usage_goals"],
    ),
    "memory_structure_migration_plan": (
        "memory_structure_migration_plan",
        [],
        ["approved_constraints"],
    ),
    "clarification_questions": (
        "clarification_questions",
        ["request"],
        ["operation_risk"],
    ),
    "propose_memory_update": (
        "propose_core_memory_update",
        [],
        ["include_trajectory"],
    ),
    "read_buffer": ("read_buffer", ["name"], []),
    "clear_buffer": ("clear_buffer", [], ["name"]),
    "save_buffer": ("save_buffer_to_volume", ["name", "path"], []),
    "load_volume": ("load_text_from_volume", ["path"], ["alias"]),
    "process_document": ("process_document", ["path"], ["alias"]),
    "write_to_file": ("write_to_file", ["path", "content"], ["append"]),
    "edit_core_memory": ("edit_core_memory", ["section", "content"], ["mode"]),
    "reset": ("reset", [], ["clear_sandbox_buffers"]),
}

# Commands whose tools may block the event loop and should be offloaded.
_BLOCKING_COMMANDS = frozenset(
    {
        "analyze_document",
        "summarize_document",
        "extract_logs",
        "grounded_answer",
        "triage_logs",
        "plan_code_change",
        "memory_tree",
        "memory_action_intent",
        "memory_structure_audit",
        "memory_structure_migration_plan",
        "clarification_questions",
        "propose_memory_update",
        "parallel_semantic_map",
        "rlm_query",
        "chunk_sandbox",
        "load_document",
        "fetch_web_document",
        "save_buffer",
        "load_volume",
        "process_document",
        "write_to_file",
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
    try:
        tool_fn = _resolve_tool(agent, tool_name)
    except AttributeError as exc:
        message = f"Command {command} is not available in the current runtime."
        if command == "parallel_semantic_map":
            message += (
                " Use analyze_document, summarize_document, extract_logs, "
                "grounded_answer, or rlm_query instead."
            )
        raise ValueError(message) from exc

    if command in _BLOCKING_COMMANDS:
        if inspect.iscoroutinefunction(tool_fn):
            return await tool_fn(**kwargs)
        result = await asyncio.to_thread(tool_fn, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    result = tool_fn(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _resolve_tool(agent: RLMReActChatAgent, tool_name: str) -> Any:
    """Find a tool by name in the agent's tool list or as a method."""
    for tool in getattr(agent, "react_tools", []):
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        if name != tool_name:
            continue
        return getattr(tool, "func", tool)
    # Fallback: direct method (e.g. reset)
    return getattr(agent, tool_name)
