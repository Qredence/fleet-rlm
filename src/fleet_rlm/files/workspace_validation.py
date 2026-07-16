"""Lexical policy for Session Workspace paths."""

from __future__ import annotations

MAX_WORKSPACE_DEPTH = 8
MAX_WORKSPACE_SEGMENT_BYTES = 255
MAX_WORKSPACE_PATH_BYTES = 1_024


class WorkspacePathError(ValueError):
    """Raised when a client-relative workspace path is unsafe."""


def normalize_workspace_path(path: str, *, allow_root: bool = False) -> str:
    """Return one validated POSIX-relative path without normalizing ambiguity."""
    if not isinstance(path, str) or not path:
        raise WorkspacePathError("workspace path is required")
    if path == ".":
        if allow_root:
            return path
        raise WorkspacePathError("workspace root is not a file")
    if path.startswith("/"):
        raise WorkspacePathError("workspace path must be relative")
    if "\\" in path or "\x00" in path:
        raise WorkspacePathError("workspace path contains an unsafe character")
    if path.startswith("./") or "//" in path or path.endswith("/"):
        raise WorkspacePathError("workspace path must be canonical")
    if len(path.encode("utf-8")) > MAX_WORKSPACE_PATH_BYTES:
        raise WorkspacePathError("workspace path is too long")

    parts = path.split("/")
    if len(parts) > MAX_WORKSPACE_DEPTH:
        raise WorkspacePathError("workspace path is too deep")
    for part in parts:
        if part in {"", ".", ".."}:
            raise WorkspacePathError("workspace path contains a relative component")
        if part == ".fleet":
            raise WorkspacePathError("workspace path uses a reserved component")
        if len(part.encode("utf-8")) > MAX_WORKSPACE_SEGMENT_BYTES:
            raise WorkspacePathError("workspace path segment is too long")
    return "/".join(parts)
