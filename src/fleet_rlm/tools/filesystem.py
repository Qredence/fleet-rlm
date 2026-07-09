"""Controlled Daytona-backed filesystem tool primitives."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from fleet_rlm.tools.paths import (
    FilesystemSafetyError,
    assert_no_detectable_symlink_escape,
    safe_join_daytona_path,
)
from fleet_rlm.tools.sessions import (
    FilesystemRoot,
    call_session_method,
    filesystem_root_base,
    require_interpreter,
    resolve_interpreter_session,
)

_DEFAULT_MAX_READ_BYTES = 200_000
_MAX_READ_BYTES = 1_000_000
_LEGACY_LIST_FILES_PATTERNS = frozenset({"", "**/*"})


def reject_legacy_list_files_pattern(pattern: str | None) -> dict[str, Any] | None:
    if pattern is None or pattern in _LEGACY_LIST_FILES_PATTERNS:
        return None
    return {
        "status": "error",
        "error": (
            "list_files no longer supports glob pattern filtering. "
            "Use root='workspace' or root='volume' for Daytona directory listings."
        ),
    }


def _entry_name(entry: Any) -> str:
    value = getattr(entry, "name", None) or getattr(entry, "path", None) or str(entry)
    return PurePosixPath(str(value).rstrip("/")).name


def _modified_at(entry: Any) -> str | None:
    value = getattr(entry, "mod_time", None) or getattr(entry, "modified_at", None)
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def list_files_impl(
    path: str = ".",
    *,
    root: FilesystemRoot | str = "workspace",
    interpreter: Any | None = None,
) -> dict[str, Any]:
    """List files and directories inside an approved Daytona root."""
    bound = require_interpreter(interpreter)
    session = resolve_interpreter_session(bound)
    base = filesystem_root_base(bound, session, root)
    resolved, display_path = safe_join_daytona_path(path, base=base)
    assert_no_detectable_symlink_escape(session, path=resolved, base=base)

    entries = call_session_method(session, "list_files", resolved)
    directories: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    for entry in entries or []:
        name = _entry_name(entry)
        if not name:
            continue
        child_path = str(PurePosixPath(display_path) / name)
        item: dict[str, Any] = {
            "name": name,
            "path": child_path,
            "modified_at": _modified_at(entry),
        }
        if bool(getattr(entry, "is_dir", False)):
            directories.append({**item, "type": "directory"})
        else:
            files.append(
                {
                    **item,
                    "type": "file",
                    "size": getattr(entry, "size", None),
                }
            )
    return {
        "status": "ok",
        "root": root,
        "path": display_path,
        "directories": directories,
        "files": files,
        "total": len(directories) + len(files),
    }


def read_file_impl(
    path: str,
    *,
    root: FilesystemRoot | str = "workspace",
    max_bytes: int = _DEFAULT_MAX_READ_BYTES,
    interpreter: Any | None = None,
) -> dict[str, Any]:
    """Read a bounded UTF-8 preview from an approved Daytona root."""
    bound = require_interpreter(interpreter)
    session = resolve_interpreter_session(bound)
    base = filesystem_root_base(bound, session, root)
    resolved, display_path = safe_join_daytona_path(path, base=base)
    assert_no_detectable_symlink_escape(session, path=resolved, base=base)

    raw = call_session_method(session, "read_file", resolved)
    raw_bytes = b"" if raw is None else raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    bounded_max = max(1, min(int(max_bytes or _DEFAULT_MAX_READ_BYTES), _MAX_READ_BYTES))
    truncated = len(raw_bytes) > bounded_max
    preview = raw_bytes[:bounded_max] if truncated else raw_bytes
    text = preview.decode("utf-8", errors="replace")
    return {
        "status": "ok",
        "root": root,
        "path": display_path,
        "content": text,
        "size": len(raw_bytes),
        "returned_bytes": len(preview),
        "truncated": truncated,
        "encoding": "utf-8-lossy" if "\ufffd" in text else "utf-8",
    }


def write_file_impl(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Deferred Phase 5 write primitive shape."""
    _ = args, kwargs
    return {
        "status": "disabled",
        "error": "write_file is disabled by policy in the Phase 5 foundation slice.",
    }


__all__ = [
    "FilesystemRoot",
    "FilesystemSafetyError",
    "list_files_impl",
    "read_file_impl",
    "reject_legacy_list_files_pattern",
    "write_file_impl",
]
