"""Storage, editing, and memory-oriented sandbox tool builders.

Buffer tools have been extracted to :mod:`.buffer_tools` and process/workspace
tools to :mod:`.process_tools`.  This module keeps volume memory operations,
file editing, and the aggregating ``build_storage_tools`` entry-point.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fleet_rlm.runtime.agent.tool_delegation import _sync_compatible_tool_callable

from .sandbox_common import (
    _adaytona_list_items,
    _adaytona_read_text,
    _adaytona_write_text,
    _aget_daytona_session,
    _aexecute_submit_ctx,
    _commit_volume_best_effort,
    _daytona_file_error,
    _persistent_roots,
    _reload_volume_best_effort,
    _resolve_path_or_error,
    _SandboxToolContext,
)

# Backwards-compat re-exports so external code that imported from here still works.
from .buffer_tools import build_buffer_tools as build_buffer_tools
from .process_tools import build_process_tools as build_process_tools

if TYPE_CHECKING:
    from ..agent.chat_agent import RLMReActChatAgent


def build_storage_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build sandbox editing, buffer, volume, workspace, and memory tools.

    Aggregates tools from :func:`build_buffer_tools`,
    :func:`build_process_tools`, and the memory/edit tools defined here.
    """
    from dspy import Tool

    ctx = _SandboxToolContext(agent=agent)
    tools: list[Any] = []

    # --- Delegated sub-builders ---
    tools.extend(build_buffer_tools(agent))
    tools.extend(build_process_tools(agent))

    # --- File editing ---

    async def edit_file(
        path: str, old_snippet: str, new_snippet: str
    ) -> dict[str, Any]:
        """Robustly edit a file by finding and replacing a unique text snippet.

        Fails if the old_snippet is not found or is not unique in the file.
        Use this over fragile `sed` commands for precise code editing.
        """
        code = """
try:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
except FileNotFoundError:
    SUBMIT(status="error", error=f"File not found: {path}")
    exit(0)

count = content.count(old_snippet)
if count == 0:
    SUBMIT(status="error", error="old_snippet not found in file")
elif count > 1:
    SUBMIT(status="error", error=f"old_snippet is ambiguous (found {count} times)")
else:
    new_content = content.replace(old_snippet, new_snippet)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
    SUBMIT(status="ok", path=path, message="File updated successfully")
"""
        return await _aexecute_submit_ctx(
            ctx,
            code,
            variables={
                "path": path,
                "old_snippet": old_snippet,
                "new_snippet": new_snippet,
            },
        )

    # --- Memory / volume operations ---

    async def memory_read(path: str) -> dict[str, Any]:
        """Read a file from persistent storage."""
        roots = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=roots.memory_root,
            allowed_root=roots.allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session(ctx)
        if daytona_session is not None:
            try:
                content = await _adaytona_read_text(daytona_session, resolved_path)
            except Exception as exc:
                return _daytona_file_error(path=resolved_path, exc=exc)
            return {
                "status": "ok",
                "path": resolved_path,
                "content": content,
                "chars": len(content),
            }

        _reload_volume_best_effort(ctx)

        code = """
try:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    SUBMIT(status="ok", path=path, content=content, chars=len(content))
except FileNotFoundError:
    SUBMIT(status="error", error=f"File not found: {path}")
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        return await _aexecute_submit_ctx(ctx, code, variables={"path": resolved_path})

    async def memory_write(path: str, content: str) -> dict[str, Any]:
        """Write content to a file in persistent storage."""
        roots = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=roots.memory_root,
            allowed_root=roots.allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session(ctx)
        if daytona_session is not None:
            try:
                await _adaytona_write_text(daytona_session, resolved_path, content)
            except Exception as exc:
                return _daytona_file_error(path=resolved_path, exc=exc)
            return {
                "status": "ok",
                "path": resolved_path,
                "chars": len(content),
            }

        code = """
import os
import subprocess
try:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        os.sync()
    except AttributeError:
        pass
    sync_rc = 0
    try:
        proc = subprocess.run(["sync", allowed_root], check=False, capture_output=True)
        sync_rc = int(proc.returncode)
    except Exception:
        sync_rc = -1
    SUBMIT(status="ok", path=path, chars=len(content), sync_rc=sync_rc)
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        result = await _aexecute_submit_ctx(
            ctx,
            code,
            variables={
                "path": resolved_path,
                "content": content,
                "allowed_root": roots.allowed_root,
            },
        )
        if result.get("status") == "ok":
            _commit_volume_best_effort(ctx)
        return result

    async def write_to_file(
        path: str, content: str, append: bool = False
    ) -> dict[str, Any]:
        """Write/append text to persistent storage with safe path handling."""
        if not append:
            return await memory_write(path=path, content=content)

        roots = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=roots.memory_root,
            allowed_root=roots.allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session(ctx)
        if daytona_session is not None:
            try:
                saved_path = await _adaytona_write_text(
                    daytona_session,
                    resolved_path,
                    content,
                    append=True,
                )
            except Exception as exc:
                return _daytona_file_error(path=resolved_path, exc=exc)
            return {
                "status": "ok",
                "path": saved_path,
                "chars": len(content),
                "mode": "append",
            }

        code = """
import os
import subprocess
try:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)
    try:
        os.sync()
    except AttributeError:
        pass
    sync_rc = 0
    try:
        proc = subprocess.run(["sync", allowed_root], check=False, capture_output=True)
        sync_rc = int(proc.returncode)
    except Exception:
        sync_rc = -1
    SUBMIT(status="ok", path=path, chars=len(content), mode="append", sync_rc=sync_rc)
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        result = await _aexecute_submit_ctx(
            ctx,
            code,
            variables={
                "path": resolved_path,
                "content": content,
                "allowed_root": roots.allowed_root,
            },
        )
        if result.get("status") == "ok":
            _commit_volume_best_effort(ctx)
        return result

    def edit_core_memory(
        section: str,
        content: str,
        mode: str = "append",
    ) -> dict[str, Any]:
        """Edit core memory via append/replace operations with validation."""
        mode_norm = mode.strip().lower()
        if mode_norm not in {"append", "replace"}:
            return {
                "status": "error",
                "error": "mode must be one of: append, replace",
            }

        message = (
            ctx.agent.core_memory_append(section, content)
            if mode_norm == "append"
            else ctx.agent.core_memory_replace(section, content)
        )
        if message.startswith("Error:"):
            return {"status": "error", "error": message}

        return {
            "status": "ok",
            "section": section,
            "mode": mode_norm,
            "message": message,
            "chars": len(ctx.agent._core_memory.get(section, "")),
        }

    async def memory_list(path: str = ".") -> dict[str, Any]:
        """List files and directories in persistent storage."""
        roots = _persistent_roots(ctx)
        try:
            if path.strip() in {"", ".", "./"}:
                resolved_path = roots.memory_root
            else:
                resolved_path, error = _resolve_path_or_error(
                    path=path,
                    default_root=roots.memory_root,
                    allowed_root=roots.allowed_root,
                )
                if error is not None:
                    return error
        except AttributeError:
            resolved_path = roots.memory_root
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session(ctx)
        if daytona_session is not None:
            try:
                items = await _adaytona_list_items(daytona_session, resolved_path)
            except Exception as exc:
                return _daytona_file_error(path=resolved_path, exc=exc)
            return {
                "status": "ok",
                "path": resolved_path,
                "items": items,
                "count": len(items),
            }

        _reload_volume_best_effort(ctx)

        code = """
import os
try:
    items = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        kind = "dir" if os.path.isdir(full) else "file"
        items.append({"name": name, "type": kind})
    SUBMIT(status="ok", path=path, items=items, count=len(items))
except Exception as e:
    SUBMIT(status="error", error=f"{type(e).__name__}: {e}")
"""
        return await _aexecute_submit_ctx(ctx, code, variables={"path": resolved_path})

    # --- Register tools ---

    tools.append(
        Tool(
            _sync_compatible_tool_callable(edit_file),
            name="edit_file",
            desc="Robustly edit a file by finding and replacing a unique text snippet",
        )
    )

    tools.extend(
        [
            Tool(
                _sync_compatible_tool_callable(memory_read),
                name="memory_read",
                desc="Read a file from durable volume memory",
            ),
            Tool(
                _sync_compatible_tool_callable(memory_write),
                name="memory_write",
                desc="Write content to a file in durable volume memory",
            ),
            Tool(
                _sync_compatible_tool_callable(write_to_file),
                name="write_to_file",
                desc="Write or append text to a file in durable volume memory",
            ),
            Tool(
                edit_core_memory,
                name="edit_core_memory",
                desc="Edit core memory blocks using append or replace mode",
            ),
            Tool(
                _sync_compatible_tool_callable(memory_list),
                name="memory_list",
                desc="List files and directories in durable volume memory",
            ),
        ]
    )

    return tools
