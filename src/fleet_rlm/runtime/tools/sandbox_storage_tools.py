"""Storage, editing, and execution-oriented sandbox tool builders."""

from __future__ import annotations

import json
from importlib import import_module
from typing import TYPE_CHECKING, Any

from fleet_rlm.runtime.agent.tool_delegation import _sync_compatible_tool_callable

from .sandbox_common import (
    _adaytona_list_items,
    _adaytona_read_text,
    _adaytona_write_text,
    _aexecute_submit_ctx,
    _buffer_volume_default_path,
    _commit_volume_best_effort,
    _daytona_file_error,
    _persistent_roots,
    _reload_volume_best_effort,
    _resolve_path_or_error,
    _SandboxToolContext,
)

if TYPE_CHECKING:
    from ..agent.chat_agent import RLMReActChatAgent


async def _aget_daytona_session_via_sandbox(ctx: _SandboxToolContext) -> Any | None:
    sandbox_module = import_module("fleet_rlm.runtime.tools.sandbox")
    return await sandbox_module._aget_daytona_session(ctx)


def build_storage_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build sandbox editing, buffer, volume, workspace, and memory tools."""
    from dspy import Tool

    ctx = _SandboxToolContext(agent=agent)
    tools: list[Any] = []

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

    async def read_buffer(name: str) -> dict[str, Any]:
        """Read the full contents of a sandbox buffer."""
        result = await _aexecute_submit_ctx(
            ctx,
            "SUBMIT(items=get_buffer(name))",
            variables={"name": name},
        )
        items = result.get("items", [])
        return {"status": "ok", "name": name, "items": items, "count": len(items)}

    async def clear_buffer(name: str = "") -> dict[str, Any]:
        """Clear one sandbox buffer (or all buffers when name is empty)."""
        if name:
            code = 'clear_buffer(name)\nSUBMIT(status="ok", scope="single", name=name)'
            variables: dict[str, Any] = {"name": name}
        else:
            code = 'clear_buffer()\nSUBMIT(status="ok", scope="all")'
            variables = {}
        return await _aexecute_submit_ctx(ctx, code, variables=variables)

    async def save_buffer_to_volume(name: str, path: str) -> dict[str, Any]:
        """Persist a sandbox buffer to persistent storage as JSON."""
        allowed_root, _memory_root, workspace_root = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=_buffer_volume_default_path(workspace_root),
            allowed_root=allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session_via_sandbox(ctx)
        if daytona_session is not None:
            result = await _aexecute_submit_ctx(
                ctx,
                "SUBMIT(items=get_buffer(name))",
                variables={"name": name},
            )
            items = result.get("items", [])
            payload = json.dumps(items, indent=2, ensure_ascii=False, default=str)
            try:
                saved_path = await _adaytona_write_text(
                    daytona_session,
                    resolved_path,
                    payload,
                )
            except Exception as exc:
                return _daytona_file_error(path=resolved_path, exc=exc)
            return {"status": "ok", "saved_path": saved_path, "item_count": len(items)}

        code = """
import json
items = get_buffer(name)
payload = json.dumps(items, indent=2, ensure_ascii=False, default=str)
saved_path = save_to_volume(path, payload)
SUBMIT(status="ok", saved_path=saved_path, item_count=len(items))
"""
        result = await _aexecute_submit_ctx(
            ctx,
            code,
            variables={"name": name, "path": resolved_path},
        )
        if result.get("status") == "ok":
            _commit_volume_best_effort(ctx)
        return result

    async def load_text_from_volume(path: str, alias: str = "active") -> dict[str, Any]:
        """Load text from persistent storage into host-side document memory."""
        allowed_root, _memory_root, workspace_root = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=workspace_root,
            allowed_root=allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session_via_sandbox(ctx)
        if daytona_session is not None:
            try:
                text = await _adaytona_read_text(daytona_session, resolved_path)
            except Exception as exc:
                return _daytona_file_error(path=resolved_path, exc=exc)
            ctx.agent._set_document(alias, text)
            ctx.agent.active_alias = alias
            return {
                "status": "ok",
                "alias": alias,
                "path": resolved_path,
                "chars": len(text),
                "lines": len(text.splitlines()),
            }

        _reload_volume_best_effort(ctx)

        result = await _aexecute_submit_ctx(
            ctx,
            'text = load_from_volume(path)\nSUBMIT(status="ok", text=text)',
            variables={"path": resolved_path},
        )
        text = str(result.get("text", ""))
        if text.startswith("[error:"):
            return {"status": "error", "error": text, "path": resolved_path}
        ctx.agent._set_document(alias, text)
        ctx.agent.active_alias = alias
        return {
            "status": "ok",
            "alias": alias,
            "path": resolved_path,
            "chars": len(text),
            "lines": len(text.splitlines()),
        }

    async def process_document(path: str, alias: str = "active") -> dict[str, Any]:
        """Load a document from volume and register it for downstream analysis."""
        loaded = await load_text_from_volume(path, alias=alias)
        if loaded.get("status") != "ok":
            return loaded
        text = ctx.agent.documents.get(alias, "")
        return {
            "status": "ok",
            "alias": alias,
            "path": loaded.get("path", path),
            "chars": len(text),
            "lines": len(text.splitlines()),
            "hint": "Use analyze_long_document or summarize_long_document for semantic processing.",
        }

    async def memory_read(path: str) -> dict[str, Any]:
        """Read a file from persistent storage."""
        allowed_root, memory_root, _workspace_root = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=memory_root,
            allowed_root=allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session_via_sandbox(ctx)
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
        allowed_root, memory_root, _workspace_root = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=memory_root,
            allowed_root=allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session_via_sandbox(ctx)
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
        proc = subprocess.run(["sync", "/data"], check=False, capture_output=True)
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
            variables={"path": resolved_path, "content": content},
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

        allowed_root, memory_root, _workspace_root = _persistent_roots(ctx)
        resolved_path, error = _resolve_path_or_error(
            path=path,
            default_root=memory_root,
            allowed_root=allowed_root,
        )
        if error is not None:
            return error
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session_via_sandbox(ctx)
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
        proc = subprocess.run(["sync", "/data"], check=False, capture_output=True)
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
            variables={"path": resolved_path, "content": content},
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
        allowed_root, memory_root, _workspace_root = _persistent_roots(ctx)
        try:
            if path.strip() in {"", ".", "./"}:
                resolved_path = memory_root
            else:
                resolved_path, error = _resolve_path_or_error(
                    path=path,
                    default_root=memory_root,
                    allowed_root=allowed_root,
                )
                if error is not None:
                    return error
        except AttributeError:
            resolved_path = memory_root
        assert resolved_path is not None

        daytona_session = await _aget_daytona_session_via_sandbox(ctx)
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

    async def run(command: str) -> dict[str, Any]:
        """Execute a bash command in the sandbox environment."""
        code = f"SUBMIT(result=run({command!r}))"
        return await _aexecute_submit_ctx(ctx, code)

    async def workspace_write(path: str, content: str) -> dict[str, Any]:
        """Write content to a file in the workspace directory."""
        code = f"SUBMIT(result=workspace_write({path!r}, {content!r}))"
        return await _aexecute_submit_ctx(ctx, code)

    async def workspace_read(path: str) -> dict[str, Any]:
        """Read content from a file in the workspace directory."""
        code = f"SUBMIT(result=workspace_read({path!r}))"
        return await _aexecute_submit_ctx(ctx, code)

    async def extract_python_ast(path: str) -> dict[str, Any]:
        """Extract structural AST JSON mapping (Classes, Methods, Functions, Docstrings) of a Python file"""
        code = f"SUBMIT(result=extract_python_ast({path!r}))"
        return await _aexecute_submit_ctx(ctx, code)

    async def start_background_process(process_id: str, command: str) -> dict[str, Any]:
        """Start a non-blocking background process (daemon) in the sandbox."""
        code = f"SUBMIT(result=start_background_process({process_id!r}, {command!r}))"
        return await _aexecute_submit_ctx(ctx, code)

    async def read_process_logs(process_id: str, tail: int = 50) -> dict[str, Any]:
        """Read the live stdout/stderr logs of an active background process."""
        code = f"SUBMIT(result=read_process_logs({process_id!r}, tail={tail}))"
        return await _aexecute_submit_ctx(ctx, code)

    async def kill_process(process_id: str) -> dict[str, Any]:
        """Terminate a running background process by its ID."""
        code = f"SUBMIT(result=kill_process({process_id!r}))"
        return await _aexecute_submit_ctx(ctx, code)

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
                _sync_compatible_tool_callable(run),
                name="run",
                desc="Execute a bash command in the sandbox environment",
            ),
            Tool(
                _sync_compatible_tool_callable(workspace_write),
                name="workspace_write",
                desc="Write content to a file in the workspace directory",
            ),
            Tool(
                _sync_compatible_tool_callable(workspace_read),
                name="workspace_read",
                desc="Read content from a file in the workspace directory",
            ),
            Tool(
                _sync_compatible_tool_callable(extract_python_ast),
                name="extract_python_ast",
                desc="Extract structural AST JSON mapping (Classes, Methods, Functions, Docstrings) of a Python file",
            ),
            Tool(
                _sync_compatible_tool_callable(start_background_process),
                name="start_background_process",
                desc="Start a non-blocking background process (like a live webserver or watch compiler) by passing an arbitrary process ID and the shell command.",
            ),
            Tool(
                _sync_compatible_tool_callable(read_process_logs),
                name="read_process_logs",
                desc="Read the latest stdout/stderr logs of an active background process.",
            ),
            Tool(
                _sync_compatible_tool_callable(kill_process),
                name="kill_process",
                desc="Terminate a running background process.",
            ),
        ]
    )

    tools.extend(
        [
            Tool(
                _sync_compatible_tool_callable(read_buffer),
                name="read_buffer",
                desc="Read the full contents of a sandbox buffer",
            ),
            Tool(
                _sync_compatible_tool_callable(clear_buffer),
                name="clear_buffer",
                desc="Clear one sandbox buffer (or all buffers when name is empty)",
            ),
            Tool(
                _sync_compatible_tool_callable(save_buffer_to_volume),
                name="save_buffer_to_volume",
                desc="Persist a sandbox buffer to persistent storage as JSON",
            ),
            Tool(
                _sync_compatible_tool_callable(load_text_from_volume),
                name="load_text_from_volume",
                desc="Load text from persistent storage into host-side document memory",
            ),
            Tool(
                _sync_compatible_tool_callable(process_document),
                name="process_document",
                desc="Load a document from persistent storage and register it for analysis",
            ),
        ]
    )

    tools.extend(
        [
            Tool(
                _sync_compatible_tool_callable(memory_read),
                name="memory_read",
                desc="Read a file from persistent storage",
            ),
            Tool(
                _sync_compatible_tool_callable(memory_write),
                name="memory_write",
                desc="Write content to a file in persistent storage",
            ),
            Tool(
                _sync_compatible_tool_callable(write_to_file),
                name="write_to_file",
                desc="Write or append text to a file in persistent storage",
            ),
            Tool(
                edit_core_memory,
                name="edit_core_memory",
                desc="Edit core memory blocks using append or replace mode",
            ),
            Tool(
                _sync_compatible_tool_callable(memory_list),
                name="memory_list",
                desc="List files and directories in persistent storage",
            ),
        ]
    )

    return tools
