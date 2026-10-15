"""Session IDs and workspace-relative paths: validated before they reach a Volume subpath or the sandbox."""

from __future__ import annotations

import re

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_NAMESPACE_RE = re.compile(r"^ns_[a-f0-9]{8}$")
_ALLOWED_WORKSPACE_ROOTS = frozenset({"files", "artifacts", "memory", "skills", "buffers", "sessions"})


def validate_session_id(session_id: str) -> str:
    """Validate a session key before it is used as a Daytona Volume subpath."""
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        raise ValueError("session_id must be 1-64 letters, digits, dots, underscores, or hyphens")
    if session_id in {".", ".."} or ".." in session_id:
        raise ValueError("session_id must not contain parent path syntax")
    return session_id


def session_volume_subpath(session_id: str) -> str:
    """Return the provider-enforced private subtree for one Session."""
    return f"sessions/{validate_session_id(session_id)}"


def _child_input_path(path: str) -> str:
    """Validate a child input path, accepting the sandbox's absolute `/workspace/` form the model sees."""
    if isinstance(path, str) and path.startswith("/workspace/"):
        path = path[len("/workspace/") :]
    clean_path = _workspace_relative_path(path)
    if not clean_path.startswith(("files/", "skills/")):
        raise ValueError("Child inputs must be files beneath files/ or skills/")
    return clean_path


def _workspace_relative_path(relative_path: str) -> str:
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or len(relative_path) > 1024
        or "\\" in relative_path
        or "\x00" in relative_path
        or any(ord(character) < 32 for character in relative_path)
    ):
        raise ValueError("Workspace path must be a non-empty POSIX-relative path")
    parts = relative_path.split("/")
    if parts[0] not in _ALLOWED_WORKSPACE_ROOTS or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Workspace path is outside the allowed files, artifacts, memory, and skills roots")
    return "/".join(parts)
