"""Daytona sandbox filesystem tools for the fleet tool registry.

Exposes module-level ``@tool_fn`` stubs that raise ``RuntimeError`` when
called without a bound ``AgentRuntime`` interpreter.  Read/list aliases route
through the canonical ``fleet_rlm.tools.filesystem`` implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fleet_rlm.runtime.tools._marker import tool_fn
from fleet_rlm.tools.filesystem import list_files_impl, read_file_impl
from fleet_rlm.tools.sessions import resolve_interpreter_session, run_sandbox_fs_call


@dataclass(slots=True)
class _SandboxFilesystemToolContext:
    """Shared tool context for sandbox filesystem operations."""

    interpreter: Any | None


def _require_interpreter(ctx: _SandboxFilesystemToolContext) -> Any:
    if ctx.interpreter is None:
        raise RuntimeError("sandbox_filesystem tools require an active AgentRuntime with a Daytona interpreter.")
    return ctx.interpreter


def _resolve_sandbox_path(ctx: _SandboxFilesystemToolContext, path: str) -> str:
    if ctx.interpreter is None:
        return path
    session = resolve_interpreter_session(ctx.interpreter)
    if session is not None and hasattr(session, "_resolve_sandbox_path"):
        return session._resolve_sandbox_path(path)
    return path


def _sandbox_list_files_impl(ctx: _SandboxFilesystemToolContext, path: str = ".") -> dict[str, Any]:
    """Compatibility alias for listing files inside the Daytona workspace."""
    return list_files_impl(path, root="workspace", interpreter=_require_interpreter(ctx))


def _sandbox_read_file_impl(ctx: _SandboxFilesystemToolContext, path: str) -> dict[str, Any]:
    """Compatibility alias for reading files inside the Daytona workspace."""
    return read_file_impl(path, root="workspace", interpreter=_require_interpreter(ctx))


def _sandbox_write_file_impl(ctx: _SandboxFilesystemToolContext, path: str, content: str) -> dict[str, Any]:
    """Write a text file to the Daytona sandbox."""
    resolved = _resolve_sandbox_path(ctx, path)
    payload = content.encode("utf-8")
    interpreter = _require_interpreter(ctx)
    session = resolve_interpreter_session(interpreter)
    if session is None:
        raise RuntimeError("No Daytona sandbox session available.")
    rebind = getattr(session, "_rebind_sandbox_if_needed", None)
    if callable(rebind):
        rebind()
    fs = getattr(getattr(session, "sandbox", None), "fs", None)
    if fs is None:
        raise RuntimeError("No Daytona filesystem available.")
    resolve = getattr(session, "_resolve_sandbox_path", None)
    upload_path = resolve(path) if callable(resolve) else resolved
    from fleet_rlm.utils.async_compat import _run_async_compat

    _run_async_compat(fs.upload_file, payload, upload_path)
    return {
        "status": "ok",
        "path": resolved,
        "bytes_written": len(payload),
    }


def _sandbox_create_directory_impl(ctx: _SandboxFilesystemToolContext, path: str) -> dict[str, Any]:
    """Create a directory in the Daytona sandbox."""
    stripped = (path or "").strip()
    if stripped and "/" not in stripped and not stripped.startswith("."):
        import uuid as _uuid

        volume_path = f"/home/daytona/memory/{stripped}"
        workspace_hint = f"<workspace>/{stripped}"
        return {
            "status": "clarification_needed",
            "message_id": f"clar-mkdir-{_uuid.uuid4().hex[:8]}",
            "question": f'Where should "{stripped}" be created?',
            "step_label": "Path clarification",
            "options": [
                {
                    "id": "volume",
                    "label": "Volume (persistent)",
                    "description": volume_path,
                },
                {
                    "id": "workspace",
                    "label": "Workspace (ephemeral)",
                    "description": workspace_hint,
                },
            ],
        }
    resolved = _resolve_sandbox_path(ctx, path)
    run_sandbox_fs_call(_require_interpreter(ctx), path, "create_folder", "755")
    return {"status": "ok", "path": resolved}


def _sandbox_delete_file_impl(ctx: _SandboxFilesystemToolContext, path: str) -> dict[str, Any]:
    """Delete a file or directory from the Daytona sandbox."""
    resolved = _resolve_sandbox_path(ctx, path)
    run_sandbox_fs_call(_require_interpreter(ctx), path, "delete_file")
    return {"status": "ok", "path": resolved, "deleted": True}


def _sandbox_move_file_impl(
    ctx: _SandboxFilesystemToolContext,
    source: str,
    destination: str,
) -> dict[str, Any]:
    """Move or rename a file or directory in the Daytona sandbox."""
    resolved_source = _resolve_sandbox_path(ctx, source)
    resolved_dest = _resolve_sandbox_path(ctx, destination)
    run_sandbox_fs_call(_require_interpreter(ctx), source, "move_files", resolved_dest)
    return {
        "status": "ok",
        "source": resolved_source,
        "destination": resolved_dest,
    }


def _sandbox_search_files_impl(ctx: _SandboxFilesystemToolContext, path: str, pattern: str) -> dict[str, Any]:
    """Search files by name pattern (glob) in the Daytona sandbox."""
    resolved = _resolve_sandbox_path(ctx, path)
    result = run_sandbox_fs_call(_require_interpreter(ctx), path, "search_files", pattern)
    files = []
    if isinstance(result, dict):
        files = result.get("files", [])
    elif hasattr(result, "files"):
        files = list(result.files)
    return {
        "status": "ok",
        "path": resolved,
        "pattern": pattern,
        "count": len(files),
        "files": files,
    }


def _sandbox_find_in_files_impl(ctx: _SandboxFilesystemToolContext, path: str, pattern: str) -> dict[str, Any]:
    """Search file contents by text pattern (grep-like) in the Daytona sandbox."""
    resolved = _resolve_sandbox_path(ctx, path)
    matches = run_sandbox_fs_call(_require_interpreter(ctx), path, "find_files", pattern)
    hits: list[dict[str, Any]] = []
    for match in matches:
        if isinstance(match, dict):
            hits.append(match)
        else:
            hits.append(
                {
                    "file": getattr(match, "file", ""),
                    "line": getattr(match, "line", None),
                    "content": getattr(match, "content", ""),
                }
            )
    return {
        "status": "ok",
        "path": resolved,
        "pattern": pattern,
        "count": len(hits),
        "hits": hits,
    }


def _sandbox_replace_in_files_impl(
    ctx: _SandboxFilesystemToolContext,
    files: list[str],
    pattern: str,
    replacement: str,
) -> dict[str, Any]:
    """Replace text in multiple files in the Daytona sandbox."""
    interpreter = _require_interpreter(ctx)
    session = resolve_interpreter_session(interpreter)
    if session is None:
        raise RuntimeError("No Daytona sandbox session available.")
    rebind = getattr(session, "_rebind_sandbox_if_needed", None)
    if callable(rebind):
        rebind()
    fs = getattr(getattr(session, "sandbox", None), "fs", None)
    if fs is None:
        raise RuntimeError("No Daytona filesystem available.")
    resolved_files = [_resolve_sandbox_path(ctx, file_path) for file_path in files]
    from fleet_rlm.utils.async_compat import _run_async_compat

    result = _run_async_compat(fs.replace_in_files, resolved_files, pattern, replacement)
    return {
        "status": "ok",
        "files": resolved_files,
        "pattern": pattern,
        "result": result if isinstance(result, dict) else {},
    }


def _sandbox_get_file_info_impl(ctx: _SandboxFilesystemToolContext, path: str) -> dict[str, Any]:
    """Get metadata for a file or directory in the Daytona sandbox."""
    resolved = _resolve_sandbox_path(ctx, path)
    info = run_sandbox_fs_call(_require_interpreter(ctx), path, "get_file_info")
    if isinstance(info, dict):
        return {"status": "ok", "path": resolved, **info}
    mod_time = getattr(info, "mod_time", None)
    mod_time_str = ""
    if mod_time is not None:
        mod_time_str = mod_time.isoformat() if hasattr(mod_time, "isoformat") else str(mod_time)
    return {
        "status": "ok",
        "path": resolved,
        "name": getattr(info, "name", ""),
        "size": getattr(info, "size", None),
        "mode": getattr(info, "mode", None),
        "is_dir": getattr(info, "is_dir", False),
        "mod_time": mod_time_str,
    }


@tool_fn
def sandbox_list_files(path: str = ".") -> dict[str, Any]:
    """List files and directories in the Daytona sandbox."""
    return _sandbox_list_files_impl(_SandboxFilesystemToolContext(interpreter=None), path=path)


@tool_fn
def sandbox_read_file(path: str) -> dict[str, Any]:
    """Read a text file from the Daytona sandbox."""
    return _sandbox_read_file_impl(_SandboxFilesystemToolContext(interpreter=None), path=path)


@tool_fn
def sandbox_write_file(path: str, content: str) -> dict[str, Any]:
    """Write a text file to the Daytona sandbox."""
    return _sandbox_write_file_impl(_SandboxFilesystemToolContext(interpreter=None), path=path, content=content)


@tool_fn
def sandbox_create_directory(path: str) -> dict[str, Any]:
    """Create a directory in the Daytona sandbox."""
    return _sandbox_create_directory_impl(_SandboxFilesystemToolContext(interpreter=None), path=path)


@tool_fn
def sandbox_delete_file(path: str) -> dict[str, Any]:
    """Delete a file or directory from the Daytona sandbox."""
    return _sandbox_delete_file_impl(_SandboxFilesystemToolContext(interpreter=None), path=path)


@tool_fn
def sandbox_move_file(source: str, destination: str) -> dict[str, Any]:
    """Move or rename a file or directory in the Daytona sandbox."""
    return _sandbox_move_file_impl(
        _SandboxFilesystemToolContext(interpreter=None),
        source=source,
        destination=destination,
    )


@tool_fn
def sandbox_search_files(path: str, pattern: str) -> dict[str, Any]:
    """Find sandbox files by glob pattern."""
    return _sandbox_search_files_impl(_SandboxFilesystemToolContext(interpreter=None), path=path, pattern=pattern)


@tool_fn
def sandbox_find_in_files(path: str, pattern: str) -> dict[str, Any]:
    """Search sandbox file contents for a text pattern."""
    return _sandbox_find_in_files_impl(_SandboxFilesystemToolContext(interpreter=None), path=path, pattern=pattern)


@tool_fn
def sandbox_replace_in_files(files: list[str], pattern: str, replacement: str) -> dict[str, Any]:
    """Replace text across multiple sandbox files."""
    return _sandbox_replace_in_files_impl(
        _SandboxFilesystemToolContext(interpreter=None),
        files=files,
        pattern=pattern,
        replacement=replacement,
    )


@tool_fn
def sandbox_get_file_info(path: str) -> dict[str, Any]:
    """Inspect metadata for a sandbox file or directory."""
    return _sandbox_get_file_info_impl(_SandboxFilesystemToolContext(interpreter=None), path=path)


__all__ = [
    "_sandbox_create_directory_impl",
    "_sandbox_delete_file_impl",
    "_sandbox_find_in_files_impl",
    "_sandbox_get_file_info_impl",
    "_sandbox_list_files_impl",
    "_sandbox_move_file_impl",
    "_sandbox_read_file_impl",
    "_sandbox_replace_in_files_impl",
    "_sandbox_search_files_impl",
    "_sandbox_write_file_impl",
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
]
