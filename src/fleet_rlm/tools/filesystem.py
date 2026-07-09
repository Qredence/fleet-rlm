"""Controlled Daytona-backed filesystem tool primitives."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import unquote

from fleet_rlm.utils.async_compat import _run_async_compat

FilesystemRoot = Literal["workspace", "volume"]
_ENCODED_TRAVERSAL_TOKENS = ("%2e%2e", "%2f", "%5c")
_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:")
_DEFAULT_MAX_READ_BYTES = 200_000
_MAX_READ_BYTES = 1_000_000


class FilesystemSafetyError(ValueError):
    """Raised when a requested Daytona path violates filesystem policy."""


def _resolve_interpreter_session(interpreter: Any) -> Any | None:
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


def _require_interpreter(interpreter: Any | None) -> Any:
    if interpreter is None:
        raise RuntimeError("filesystem tools require a bound Daytona interpreter.")
    return interpreter


def _root_base(interpreter: Any, session: Any | None, root: FilesystemRoot | str) -> str:
    if root == "workspace":
        base = getattr(session, "workspace_path", None) or getattr(interpreter, "workspace_path", None)
        return str(base or "/workspace")
    if root == "volume":
        base = getattr(interpreter, "volume_mount_path", None) or getattr(session, "volume_mount_path", None)
        if not base:
            raise RuntimeError("volume filesystem tools require a bound Daytona volume mount path.")
        return str(base)
    raise FilesystemSafetyError(f"Unsupported filesystem root: {root!r}")


def _safe_join(path: str, *, base: str) -> tuple[str, str]:
    raw = str(path or ".").strip() or "."
    lowered = raw.lower()
    if any(token in lowered for token in _ENCODED_TRAVERSAL_TOKENS):
        raise FilesystemSafetyError("Path traversal is not allowed.")
    if "\\" in raw:
        raise FilesystemSafetyError("Backslash paths are not allowed.")
    if _DRIVE_PATH_RE.match(raw):
        raise FilesystemSafetyError("Host drive paths are not allowed.")

    decoded = unquote(raw)
    if "\\" in decoded:
        raise FilesystemSafetyError("Backslash paths are not allowed.")
    candidate = PurePosixPath(decoded)
    if ".." in candidate.parts:
        raise FilesystemSafetyError("Path traversal is not allowed.")

    normalized_base = str(PurePosixPath(base)).rstrip("/") or "/"
    if candidate.is_absolute():
        candidate_text = str(candidate)
        if candidate_text != normalized_base and not candidate_text.startswith(f"{normalized_base}/"):
            raise FilesystemSafetyError("Absolute paths must stay inside the selected Daytona root.")
        resolved = candidate_text
    else:
        resolved = str(PurePosixPath(normalized_base) / candidate)

    if resolved != normalized_base and not resolved.startswith(f"{normalized_base}/"):
        raise FilesystemSafetyError("Resolved path escapes the selected Daytona root.")
    return resolved, resolved


def _call_session_method(session: Any, method_name: str, path: str) -> Any:
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


def _assert_no_detectable_symlink_escape(session: Any | None, *, path: str, base: str) -> None:
    if session is None:
        return
    info: Any | None = None
    method = getattr(session, "get_file_info", None)
    if callable(method):
        try:
            info = method(path)
        except Exception:
            info = None
    if info is None:
        fs = getattr(getattr(session, "sandbox", None), "fs", None)
        method = getattr(fs, "get_file_info", None)
        if callable(method):
            try:
                info = _run_async_compat(method, path)
            except Exception:
                info = None
    if info is None:
        return
    for attr in ("real_path", "resolved_path", "target_path"):
        real_path = getattr(info, attr, None)
        if not real_path and isinstance(info, dict):
            real_path = info.get(attr)
        if not real_path:
            continue
        normalized_base = str(PurePosixPath(base)).rstrip("/") or "/"
        resolved = str(PurePosixPath(str(real_path)))
        if resolved != normalized_base and not resolved.startswith(f"{normalized_base}/"):
            raise FilesystemSafetyError("Path resolves outside the selected Daytona root.")


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


def list_files_impl(
    path: str = ".",
    *,
    root: FilesystemRoot | str = "workspace",
    interpreter: Any | None = None,
) -> dict[str, Any]:
    """List files and directories inside an approved Daytona root."""
    bound = _require_interpreter(interpreter)
    session = _resolve_interpreter_session(bound)
    base = _root_base(bound, session, root)
    resolved, display_path = _safe_join(path, base=base)
    _assert_no_detectable_symlink_escape(session, path=resolved, base=base)

    entries = _call_session_method(session, "list_files", resolved)
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
    bound = _require_interpreter(interpreter)
    session = _resolve_interpreter_session(bound)
    base = _root_base(bound, session, root)
    resolved, display_path = _safe_join(path, base=base)
    _assert_no_detectable_symlink_escape(session, path=resolved, base=base)

    raw = _call_session_method(session, "read_file", resolved)
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
