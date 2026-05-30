"""Runtime binding helpers for fleet tools that need live agent state."""

from __future__ import annotations

import functools
import re
from collections.abc import Callable
from typing import Any

from fleet_rlm.runtime.tools.document_tools import _load_document_impl
from fleet_rlm.runtime.tools.knowledge_tools import _search_knowledge_impl
from fleet_rlm.runtime.tools.rlm_delegate import (
    delegate_to_rlm as _delegate_to_rlm,
)
from fleet_rlm.runtime.tools.rlm_delegate import (
    delegate_to_rlm_batched as _delegate_to_rlm_batched,
)
from fleet_rlm.runtime.tools.sandbox_filesystem import (
    _sandbox_create_directory_impl,
    _sandbox_delete_file_impl,
    _sandbox_find_in_files_impl,
    _sandbox_get_file_info_impl,
    _sandbox_list_files_impl,
    _sandbox_move_file_impl,
    _sandbox_read_file_impl,
    _sandbox_replace_in_files_impl,
    _sandbox_search_files_impl,
    _sandbox_write_file_impl,
    _SandboxFilesystemToolContext,
)
from fleet_rlm.runtime.tools.skill_tools import _load_skill_impl
from fleet_rlm.runtime.tools.volume_memory_tools import (
    _recall_impl,
    _remember_impl,
)

INTERPRETER_TOOL_NAMES = frozenset(
    {
        "clear_buffer",
        "delegate_to_rlm",
        "delegate_to_rlm_batched",
        "execute_code",
        "load_skill",
        "read_buffer",
        "recursive_workspace",
        "remember",
        "recall",
        "sandbox_create_directory",
        "sandbox_delete_file",
        "sandbox_find_in_files",
        "sandbox_get_file_info",
        "sandbox_list_files",
        "sandbox_move_file",
        "sandbox_read_file",
        "sandbox_replace_in_files",
        "sandbox_search_files",
        "sandbox_write_file",
        "search_knowledge",
        "write_buffer",
    }
)


def _tool_name(tool: Any) -> str | None:
    return getattr(tool, "name", None) or getattr(getattr(tool, "func", tool), "__name__", None)


def _context_with_remote_document(query: str, context: str) -> str:
    match = re.search(r"https?://\S+", f"{query}\n{context}")
    if match is None:
        return context
    url = match.group(0).rstrip(").,;]")
    from fleet_rlm.runtime.tools.document_tools import fetch_document_text

    fetch_result = fetch_document_text(url)
    if fetch_result.get("status") != "ok":
        return (
            context + f"\n\nNote: Attempted to fetch {url} for recursive workspace "
            f"context but failed: {fetch_result.get('error', 'unknown error')}"
        ).strip()
    text = str(fetch_result.get("text", ""))
    char_count = int(fetch_result.get("char_count", len(text)))
    return (
        context + f"\n\n--- Document fetched from {url} ({char_count} chars) ---\n{text}\n--- End of document ---"
    ).strip()


def coerce_sandbox_result(raw: Any) -> dict[str, Any]:
    """Normalize Daytona interpreter execution results into a tool payload."""
    payload = getattr(raw, "output", raw)
    if isinstance(payload, dict):
        result = dict(payload)
        result.setdefault("status", "ok")
        return result
    if payload is None:
        return {"status": "ok"}
    return {"status": "ok", "output": str(payload)}


def _tool_description(tool: Any) -> str:
    return getattr(tool, "desc", None) or getattr(getattr(tool, "func", tool), "__doc__", "") or ""


def _wrap_tool(tool: Any, func: Callable[..., Any]) -> Any:
    from dspy import Tool

    name = _tool_name(tool)
    if name is None:
        return tool
    return Tool(func, name=name, desc=_tool_description(tool))


def execute_sandbox_tool(
    interpreter: Any,
    code: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute code through an interpreter and normalize the result."""
    raw = interpreter.execute(code, variables or {})
    return coerce_sandbox_result(raw)


def _bound_runtime_tool_factories(
    *,
    runtime: Any,
    interpreter: Any | None,
) -> dict[str, Callable[..., Any]]:
    factories: dict[str, Callable[..., Any]] = {}

    def read_core_memory(key: str = "") -> dict[str, Any]:
        if key:
            return {
                "status": "ok",
                "key": key,
                "value": runtime.core_memory.get(key),
            }
        return {"status": "ok", "entries": dict(runtime.core_memory)}

    def write_core_memory(key: str, value: str) -> dict[str, Any]:
        runtime.core_memory[key] = value
        return {"status": "ok", "key": key, "value": value}

    factories["read_core_memory"] = read_core_memory
    factories["write_core_memory"] = write_core_memory

    _interp = interpreter if interpreter is not None else getattr(runtime, "interpreter", None)
    volume_mount_path: str = str(getattr(_interp, "volume_mount_path", "") or "")
    agent_depth: int = int(getattr(runtime, "agent_depth", 0) or 0)

    if volume_mount_path:

        def remember(key: str, value: str) -> dict[str, Any]:
            return _remember_impl(key, value, volume_mount_path=volume_mount_path, agent_depth=agent_depth)

        def recall(query: str) -> dict[str, Any]:
            return _recall_impl(query, volume_mount_path=volume_mount_path)

        def load_document(source: str, alias: str = "active") -> dict[str, Any]:
            return _load_document_impl(source, alias=alias, volume_mount_path=volume_mount_path).model_dump()

        def search_knowledge(query: str, max_results: int = 20) -> dict[str, Any]:
            return _search_knowledge_impl(
                query, volume_mount_path=volume_mount_path, max_results=max_results
            ).model_dump()

        def load_skill(name: str) -> dict[str, Any]:
            return _load_skill_impl(name, volume_mount_path=volume_mount_path).model_dump()

        factories["load_document"] = load_document
        factories["load_skill"] = load_skill
        factories["remember"] = remember
        factories["recall"] = recall
        factories["search_knowledge"] = search_knowledge

    if interpreter is None:
        return factories

    sandbox_ctx = _SandboxFilesystemToolContext(interpreter=interpreter)

    def execute_code(
        code: str,
        variables: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        _ = timeout
        return coerce_sandbox_result(interpreter.execute(code, variables or {}))

    def read_buffer(name: str = "default") -> dict[str, Any]:
        return execute_sandbox_tool(
            interpreter,
            "items = get_buffer(buffer_name)\nSUBMIT(status='ok', name=buffer_name, items=items)",
            {"buffer_name": name},
        )

    def write_buffer(name: str, content: str) -> dict[str, Any]:
        return execute_sandbox_tool(
            interpreter,
            "add_buffer(buffer_name, content)\n"
            "items = get_buffer(buffer_name)\n"
            "SUBMIT(status='ok', name=buffer_name, item_count=len(items))",
            {"buffer_name": name, "content": content},
        )

    def clear_buffer(name: str = "default") -> dict[str, Any]:
        return execute_sandbox_tool(
            interpreter,
            "clear_buffer(buffer_name)\nSUBMIT(status='ok', name=buffer_name)",
            {"buffer_name": name},
        )

    def delegate_to_rlm(query: str, context: str = "", document_url: str = "") -> dict[str, Any]:
        return _delegate_to_rlm(
            query=query,
            context=context,
            document_url=document_url,
            interpreter=interpreter,
        )

    def delegate_to_rlm_batched(queries: list[str], context: str = "", document_url: str = "") -> dict[str, Any]:
        return _delegate_to_rlm_batched(
            queries=queries,
            context=context,
            document_url=document_url,
            interpreter=interpreter,
        )

    factories.update(
        {
            "clear_buffer": clear_buffer,
            "delegate_to_rlm": delegate_to_rlm,
            "delegate_to_rlm_batched": delegate_to_rlm_batched,
            "execute_code": execute_code,
            "read_buffer": read_buffer,
            "sandbox_list_files": functools.partial(_sandbox_list_files_impl, sandbox_ctx),
            "sandbox_read_file": functools.partial(_sandbox_read_file_impl, sandbox_ctx),
            "sandbox_write_file": functools.partial(_sandbox_write_file_impl, sandbox_ctx),
            "sandbox_create_directory": functools.partial(_sandbox_create_directory_impl, sandbox_ctx),
            "sandbox_delete_file": functools.partial(_sandbox_delete_file_impl, sandbox_ctx),
            "sandbox_move_file": functools.partial(_sandbox_move_file_impl, sandbox_ctx),
            "sandbox_search_files": functools.partial(_sandbox_search_files_impl, sandbox_ctx),
            "sandbox_find_in_files": functools.partial(_sandbox_find_in_files_impl, sandbox_ctx),
            "sandbox_replace_in_files": functools.partial(_sandbox_replace_in_files_impl, sandbox_ctx),
            "sandbox_get_file_info": functools.partial(_sandbox_get_file_info_impl, sandbox_ctx),
            "write_buffer": write_buffer,
        }
    )

    def recursive_workspace(
        query: str,
        context: str = "",
        max_passes: int = 3,
    ) -> dict[str, Any]:
        """Run a multi-pass recursive analysis with decomposition and verification."""
        from fleet_rlm.integrations.daytona.isolation import DaytonaEvidenceSink
        from fleet_rlm.runtime.modules.workspace import RecursiveWorkspaceModule

        module = RecursiveWorkspaceModule(
            interpreter=interpreter,
            max_passes=max_passes,
            verbose=False,
            sub_lm=getattr(interpreter, "sub_lm", None),
            evidence_sink=DaytonaEvidenceSink(interpreter),
        )
        prediction = module(
            user_request=query,
            context=_context_with_remote_document(query, context),
        )
        return {
            "status": str(getattr(prediction, "status", "ok")),
            "answer": str(getattr(prediction, "answer", "")),
            "passes": int(getattr(prediction, "passes", 0)),
        }

    factories["recursive_workspace"] = recursive_workspace
    return factories


def bind_runtime_tools(
    tools: list[Any],
    *,
    runtime: Any,
    interpreter: Any | None,
) -> list[Any]:
    """Bind runtime-backed stubs and remove unavailable interpreter tools."""
    bound_factories = _bound_runtime_tool_factories(
        runtime=runtime,
        interpreter=interpreter,
    )
    result: list[Any] = []
    for tool in tools:
        name = _tool_name(tool)
        if name in bound_factories:
            result.append(_wrap_tool(tool, bound_factories[name]))
            continue
        if interpreter is None and name in INTERPRETER_TOOL_NAMES:
            continue
        result.append(tool)
    return result


__all__ = [
    "INTERPRETER_TOOL_NAMES",
    "bind_runtime_tools",
    "coerce_sandbox_result",
    "execute_sandbox_tool",
]
