"""Daytona sandbox filesystem tools for the fleet tool registry.

Exposes module-level ``@tool_fn`` stubs that raise ``RuntimeError`` when
called without a bound ``AgentRuntime`` interpreter.  The internal ``_impl``
variants are used by ``AgentRuntime`` to create bound ``dspy.Tool`` instances
when an interpreter is present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fleet_rlm.integrations.daytona.async_compat import (
    _await_if_needed,
    _run_async_compat,
)
from fleet_rlm.runtime.tools._marker import tool_fn

# ---------------------------------------------------------------------------
# Tool context
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _SandboxFilesystemToolContext:
    """Shared tool context for sandbox filesystem operations."""

    interpreter: Any | None


# ---------------------------------------------------------------------------
# Interpreter / sandbox resolution helpers
# ---------------------------------------------------------------------------


def _get_sandbox_fs(ctx: _SandboxFilesystemToolContext) -> Any:
    """Return the ``sandbox.fs`` object from the interpreter."""
    if ctx.interpreter is None:
        raise RuntimeError("sandbox_filesystem tools require an active AgentRuntime with a Daytona interpreter.")
    session = getattr(ctx.interpreter, "_session", None)
    if session is None:
        aget = getattr(ctx.interpreter, "aget_session", None)
        if callable(aget):
            session = _run_async_compat(aget)
    if session is None:
        raise RuntimeError("No Daytona sandbox session available.")
    sandbox = getattr(session, "sandbox", None)
    if sandbox is None:
        raise RuntimeError("No Daytona sandbox available.")
    fs = getattr(sandbox, "fs", None)
    if fs is None:
        raise RuntimeError("No Daytona filesystem available.")
    return fs


def _get_sandbox_session(ctx: _SandboxFilesystemToolContext) -> Any:
    """Return the active Daytona sandbox session from the interpreter."""
    if ctx.interpreter is None:
        raise RuntimeError("sandbox_filesystem tools require an active AgentRuntime with a Daytona interpreter.")
    session = getattr(ctx.interpreter, "_session", None)
    if session is None:
        aget = getattr(ctx.interpreter, "aget_session", None)
        if callable(aget):
            session = _run_async_compat(aget)
    if session is None:
        raise RuntimeError("No Daytona sandbox session available.")
    return session


def _resolve_sandbox_path(ctx: _SandboxFilesystemToolContext, path: str) -> str:
    """Resolve *path* relative to the interpreter workspace path."""
    if ctx.interpreter is None:
        return path
    session = getattr(ctx.interpreter, "_session", None)
    if session is None:
        aget = getattr(ctx.interpreter, "aget_session", None)
        if callable(aget):
            session = _run_async_compat(aget)
    if session is not None and hasattr(session, "_resolve_sandbox_path"):
        return session._resolve_sandbox_path(path)
    return path


def _run_session_fs_call(
    ctx: _SandboxFilesystemToolContext,
    path: str,
    method_name: str,
    *args: Any,
) -> Any:
    """Run a sandbox filesystem call after rebinding stale SDK handles.

    Daytona async SDK objects are event-loop-bound.  ReAct tool calls often run
    from worker threads, so using the raw ``sandbox.fs`` handle can raise
    "Future attached to a different loop".  The session knows how to refresh the
    sandbox handle for the current loop; call through it before touching ``fs``.
    """
    session = _get_sandbox_session(ctx)

    async def _invoke() -> Any:
        rebind = getattr(session, "_arebind_sandbox_if_needed", None)
        if callable(rebind):
            await rebind()
        sandbox = getattr(session, "sandbox", None)
        fs = getattr(sandbox, "fs", None)
        if fs is None:
            raise RuntimeError("No Daytona filesystem available.")
        resolve = getattr(session, "_resolve_sandbox_path", None)
        resolved = resolve(path) if callable(resolve) else _resolve_sandbox_path(ctx, path)
        method = getattr(fs, method_name)
        return await _await_if_needed(method(resolved, *args))

    return _run_async_compat(_invoke)


# ---------------------------------------------------------------------------
# Internal implementations (bound by AgentRuntime)
# ---------------------------------------------------------------------------


def _sandbox_list_files_impl(ctx: _SandboxFilesystemToolContext, path: str = ".") -> dict[str, Any]:
    """List files and directories in the Daytona sandbox."""
    resolved = _resolve_sandbox_path(ctx, path)
    entries = _run_session_fs_call(ctx, path, "list_files")
    files: list[dict[str, Any]] = []
    dirs: list[dict[str, Any]] = []
    for entry in entries:
        name = getattr(entry, "name", "") or getattr(entry, "path", "")
        if not name:
            continue
        is_dir = bool(getattr(entry, "is_dir", False))
        item: dict[str, Any] = {"name": name}
        mod_time = getattr(entry, "mod_time", None)
        if mod_time is not None:
            if hasattr(mod_time, "isoformat"):
                item["modified_at"] = mod_time.isoformat()
            else:
                item["modified_at"] = str(mod_time)
        if is_dir:
            dirs.append({**item, "type": "directory"})
        else:
            item["size"] = getattr(entry, "size", None)
            files.append({**item, "type": "file"})
    return {
        "status": "ok",
        "path": resolved,
        "directories": dirs,
        "files": files,
        "total": len(dirs) + len(files),
    }


def _sandbox_read_file_impl(ctx: _SandboxFilesystemToolContext, path: str) -> dict[str, Any]:
    """Read a text file from the Daytona sandbox."""
    resolved = _resolve_sandbox_path(ctx, path)
    raw = _run_session_fs_call(ctx, path, "download_file")
    if raw is None:
        content = ""
    elif isinstance(raw, str):
        content = raw
    else:
        content = bytes(raw).decode("utf-8", errors="replace")
    return {
        "status": "ok",
        "path": resolved,
        "content": content,
        "size": len(content.encode("utf-8")),
    }


def _sandbox_write_file_impl(ctx: _SandboxFilesystemToolContext, path: str, content: str) -> dict[str, Any]:
    """Write a text file to the Daytona sandbox."""
    resolved = _resolve_sandbox_path(ctx, path)
    payload = content.encode("utf-8")
    session = _get_sandbox_session(ctx)

    async def _upload() -> Any:
        rebind = getattr(session, "_arebind_sandbox_if_needed", None)
        if callable(rebind):
            await rebind()
        fs = getattr(getattr(session, "sandbox", None), "fs", None)
        if fs is None:
            raise RuntimeError("No Daytona filesystem available.")
        resolve = getattr(session, "_resolve_sandbox_path", None)
        upload_path = resolve(path) if callable(resolve) else resolved
        return await _await_if_needed(fs.upload_file(payload, upload_path))

    _run_async_compat(_upload)
    return {
        "status": "ok",
        "path": resolved,
        "bytes_written": len(payload),
    }


def _sandbox_create_directory_impl(ctx: _SandboxFilesystemToolContext, path: str) -> dict[str, Any]:
    """Create a directory in the Daytona sandbox."""
    # Detect a bare relative name (no path separator) and ask for clarification
    # so the agent can confirm whether the user means the persistent volume or
    # the ephemeral workspace.
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
    _run_session_fs_call(ctx, path, "create_folder", "755")
    return {"status": "ok", "path": resolved}


def _sandbox_delete_file_impl(ctx: _SandboxFilesystemToolContext, path: str) -> dict[str, Any]:
    """Delete a file or directory from the Daytona sandbox."""
    resolved = _resolve_sandbox_path(ctx, path)
    _run_session_fs_call(ctx, path, "delete_file")
    return {"status": "ok", "path": resolved, "deleted": True}


def _sandbox_move_file_impl(
    ctx: _SandboxFilesystemToolContext,
    source: str,
    destination: str,
) -> dict[str, Any]:
    """Move or rename a file or directory in the Daytona sandbox."""
    resolved_source = _resolve_sandbox_path(ctx, source)
    resolved_dest = _resolve_sandbox_path(ctx, destination)
    _run_session_fs_call(ctx, source, "move_files", resolved_dest)
    return {
        "status": "ok",
        "source": resolved_source,
        "destination": resolved_dest,
    }


def _sandbox_search_files_impl(ctx: _SandboxFilesystemToolContext, path: str, pattern: str) -> dict[str, Any]:
    """Search files by name pattern (glob) in the Daytona sandbox."""
    resolved = _resolve_sandbox_path(ctx, path)
    result = _run_session_fs_call(ctx, path, "search_files", pattern)
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
    matches = _run_session_fs_call(ctx, path, "find_files", pattern)
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
    fs = _get_sandbox_fs(ctx)
    resolved_files = [_resolve_sandbox_path(ctx, f) for f in files]
    result = _run_async_compat(lambda: _await_if_needed(fs.replace_in_files(resolved_files, pattern, replacement)))
    return {
        "status": "ok",
        "files": resolved_files,
        "pattern": pattern,
        "result": result if isinstance(result, dict) else {},
    }


def _sandbox_get_file_info_impl(ctx: _SandboxFilesystemToolContext, path: str) -> dict[str, Any]:
    """Get metadata for a file or directory in the Daytona sandbox."""
    fs = _get_sandbox_fs(ctx)
    resolved = _resolve_sandbox_path(ctx, path)
    info = _run_async_compat(lambda: _await_if_needed(fs.get_file_info(resolved)))
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


# ---------------------------------------------------------------------------
# Public stubs (discover_tools collects these; AgentRuntime binds them)
# ---------------------------------------------------------------------------


@tool_fn
def sandbox_list_files(path: str = ".") -> dict[str, Any]:
    """List files and directories in the Daytona sandbox.

    Args:
        path: Directory path to list. Defaults to the current workspace directory.

    Returns:
        Dictionary with ``status``, ``path``, ``directories``, ``files``, and ``total``.
    """
    ctx = _SandboxFilesystemToolContext(interpreter=None)
    return _sandbox_list_files_impl(ctx, path=path)


@tool_fn
def sandbox_read_file(path: str) -> dict[str, Any]:
    """Read a text file from the Daytona sandbox.

    Args:
        path: Path to the file to read.

    Returns:
        Dictionary with ``status``, ``path``, ``content``, and ``size``.
    """
    ctx = _SandboxFilesystemToolContext(interpreter=None)
    return _sandbox_read_file_impl(ctx, path=path)


@tool_fn
def sandbox_write_file(path: str, content: str) -> dict[str, Any]:
    """Write a text file to the Daytona sandbox.

    Args:
        path: Destination path in the sandbox.
        content: Text content to write.

    Returns:
        Dictionary with ``status``, ``path``, and ``bytes_written``.
    """
    ctx = _SandboxFilesystemToolContext(interpreter=None)
    return _sandbox_write_file_impl(ctx, path=path, content=content)


@tool_fn
def sandbox_create_directory(path: str) -> dict[str, Any]:
    """Create a directory in the Daytona sandbox.

    Args:
        path: Directory path to create.

    Returns:
        Dictionary with ``status`` and ``path``.
    """
    ctx = _SandboxFilesystemToolContext(interpreter=None)
    return _sandbox_create_directory_impl(ctx, path=path)


@tool_fn
def sandbox_delete_file(path: str) -> dict[str, Any]:
    """Delete a file or directory from the Daytona sandbox.

    Args:
        path: Path to delete.

    Returns:
        Dictionary with ``status``, ``path``, and ``deleted``.
    """
    ctx = _SandboxFilesystemToolContext(interpreter=None)
    return _sandbox_delete_file_impl(ctx, path=path)


@tool_fn
def sandbox_move_file(source: str, destination: str) -> dict[str, Any]:
    """Move or rename a file or directory in the Daytona sandbox.

    Args:
        source: Source path.
        destination: Destination path.

    Returns:
        Dictionary with ``status``, ``source``, and ``destination``.
    """
    ctx = _SandboxFilesystemToolContext(interpreter=None)
    return _sandbox_move_file_impl(ctx, source=source, destination=destination)


@tool_fn
def sandbox_search_files(path: str, pattern: str) -> dict[str, Any]:
    """Search files by name pattern (glob) in the Daytona sandbox.

    Args:
        path: Root directory to search.
        pattern: Glob pattern (e.g. ``"*.py"``).

    Returns:
        Dictionary with ``status``, ``path``, ``pattern``, ``count``, and ``files``.
    """
    ctx = _SandboxFilesystemToolContext(interpreter=None)
    return _sandbox_search_files_impl(ctx, path=path, pattern=pattern)


@tool_fn
def sandbox_find_in_files(path: str, pattern: str) -> dict[str, Any]:
    """Search file contents by text pattern (grep-like) in the Daytona sandbox.

    Args:
        path: Root directory to search.
        pattern: Text pattern to find.

    Returns:
        Dictionary with ``status``, ``path``, ``pattern``, ``count``, and ``hits``.
    """
    ctx = _SandboxFilesystemToolContext(interpreter=None)
    return _sandbox_find_in_files_impl(ctx, path=path, pattern=pattern)


@tool_fn
def sandbox_replace_in_files(files: list[str], pattern: str, replacement: str) -> dict[str, Any]:
    """Replace text in multiple files in the Daytona sandbox.

    Args:
        files: List of file paths to process.
        pattern: Text to search for.
        replacement: Replacement text.

    Returns:
        Dictionary with ``status``, ``files``, ``pattern``, and ``result``.
    """
    ctx = _SandboxFilesystemToolContext(interpreter=None)
    return _sandbox_replace_in_files_impl(ctx, files=files, pattern=pattern, replacement=replacement)


@tool_fn
def sandbox_get_file_info(path: str) -> dict[str, Any]:
    """Get metadata for a file or directory in the Daytona sandbox.

    Args:
        path: Path to inspect.

    Returns:
        Dictionary with ``status``, ``path``, ``name``, ``size``, ``mode``, ``is_dir``, and ``mod_time``.
    """
    ctx = _SandboxFilesystemToolContext(interpreter=None)
    return _sandbox_get_file_info_impl(ctx, path=path)


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
