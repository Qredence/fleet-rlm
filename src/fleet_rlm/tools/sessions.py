"""Shared Daytona interpreter session helpers for fleet_rlm.tools."""

from __future__ import annotations

from typing import Any, Literal

from fleet_rlm.utils.async_compat import _run_async_compat

FilesystemRoot = Literal["workspace", "volume"]


def resolve_interpreter_session(interpreter: Any) -> Any | None:
    session = getattr(interpreter, "_session", None)
    if session is not None:
        return session
    ensure_sync = getattr(interpreter, "_ensure_session_sync", None)
    if callable(ensure_sync):
        return ensure_sync()
    aget = getattr(interpreter, "aget_session", None)
    if callable(aget):
        return _run_async_compat(aget)
    return getattr(interpreter, "session", None)


def require_interpreter(interpreter: Any | None) -> Any:
    if interpreter is None:
        raise RuntimeError("filesystem tools require a bound Daytona interpreter.")
    return interpreter


def filesystem_root_base(interpreter: Any, session: Any | None, root: FilesystemRoot | str) -> str:
    from fleet_rlm.tools.paths import FilesystemSafetyError

    if root == "workspace":
        base = getattr(session, "workspace_path", None) or getattr(interpreter, "workspace_path", None)
        return str(base or "/workspace")
    if root == "volume":
        base = getattr(interpreter, "volume_mount_path", None) or getattr(session, "volume_mount_path", None)
        if not base:
            raise RuntimeError("volume filesystem tools require a bound Daytona volume mount path.")
        return str(base)
    raise FilesystemSafetyError(f"Unsupported filesystem root: {root!r}")


def call_session_method(session: Any, method_name: str, path: str) -> Any:
    method = getattr(session, method_name, None)
    if callable(method):
        return method(path)
    async_method = getattr(session, f"a{method_name}", None)
    if callable(async_method):
        return _run_async_compat(async_method, path)
    sandbox = getattr(session, "sandbox", None)
    fs = getattr(sandbox, "fs", None)
    if fs is None:
        raise RuntimeError("No Daytona filesystem available.")
    sdk_method_name = "download_file" if method_name == "read_file" else method_name
    method = getattr(fs, sdk_method_name)
    return _run_async_compat(method, path)


def run_sandbox_fs_call(
    interpreter: Any,
    path: str,
    method_name: str,
    *args: Any,
) -> Any:
    """Run a sandbox filesystem SDK call after rebinding stale handles."""
    session = resolve_interpreter_session(interpreter)
    if session is None:
        raise RuntimeError("No Daytona sandbox session available.")
    rebind = getattr(session, "_rebind_sandbox_if_needed", None)
    if callable(rebind):
        rebind()
    sandbox = getattr(session, "sandbox", None)
    fs = getattr(sandbox, "fs", None)
    if fs is None:
        raise RuntimeError("No Daytona filesystem available.")
    resolve = getattr(session, "_resolve_sandbox_path", None)
    resolved = resolve(path) if callable(resolve) else path
    method = getattr(fs, method_name)
    return _run_async_compat(method, resolved, *args)


__all__ = [
    "FilesystemRoot",
    "call_session_method",
    "filesystem_root_base",
    "require_interpreter",
    "resolve_interpreter_session",
    "run_sandbox_fs_call",
]
