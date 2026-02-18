"""Volume and workspace persistence helpers for Modal sandbox.

These functions provide file persistence within the Modal sandbox environment,
using the mounted volume at /data.
"""

from __future__ import annotations

import glob
import os
import subprocess


def _resolve_volume_path(path: str) -> tuple[str | None, str | None]:
    """Resolve and validate a path stays under ``/data``.

    Args:
        path: Path to resolve (relative to /data or absolute under /data).

    Returns:
        Tuple of (resolved_path, error_message). If successful, error_message is None.
        If failed, resolved_path is None and error_message contains the reason.
    """
    base = "/data"
    base_real = os.path.realpath(base)
    raw = str(path or "").strip()
    if not raw:
        return None, "[error: volume path cannot be empty]"

    # Relative paths are rooted at /data, absolute paths must already
    # be under /data.
    joined = (
        os.path.normpath(raw)
        if os.path.isabs(raw)
        else os.path.normpath(os.path.join(base, raw))
    )
    resolved = os.path.realpath(joined)
    if resolved != base_real and not resolved.startswith(base_real + os.sep):
        return None, f"[error: invalid volume path: {raw}]"
    return resolved, None


def save_to_volume(path: str, content: str) -> str:
    """Write *content* to ``/data/<path>`` if volume is mounted.

    Args:
        path: Path relative to /data (or absolute under /data).
        content: Text content to write.

    Returns:
        The full path written, or an error string starting with '[error:'.
    """
    base = "/data"
    if not os.path.isdir(base):
        return "[error: no volume mounted at /data]"

    full, path_error = _resolve_volume_path(path)
    if path_error is not None or full is None:
        return path_error or "[error: invalid volume path]"

    os.makedirs(os.path.dirname(full) or base, exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)

    # Best-effort flush hints for mounted volumes.
    try:
        os.sync()
    except AttributeError:
        pass
    try:
        subprocess.run(["sync", "/data"], check=False, capture_output=True)
    except Exception:
        pass

    return full


def load_from_volume(path: str) -> str:
    """Read text from ``/data/<path>``.

    Args:
        path: Path relative to /data (or absolute under /data).

    Returns:
        The file contents, or an error string starting with '[error:'.
    """
    full, path_error = _resolve_volume_path(path)
    if path_error is not None or full is None:
        return path_error or "[error: invalid volume path]"

    if not os.path.isfile(full):
        return f"[error: file not found: {full}]"
    with open(full, encoding="utf-8") as fh:
        return fh.read()


# ------ Workspace helpers for stateful agent sessions ------

WORKSPACE_BASE = "/data/workspace"


def _resolve_workspace_path(path: str) -> tuple[str | None, str | None]:
    """Resolve and validate a workspace path stays under /data/workspace.

    Args:
        path: Path to resolve (relative to workspace base).

    Returns:
        Tuple of (resolved_path, error_message). If successful, error_message is None.
        If failed, resolved_path is None and error_message contains the reason.
    """
    base = WORKSPACE_BASE
    base_real = os.path.realpath(base)
    raw = str(path or "").strip()
    if not raw:
        return None, "[error: workspace path cannot be empty]"

    resolved = os.path.realpath(os.path.normpath(os.path.join(base, raw)))
    if resolved != base_real and not resolved.startswith(base_real + os.sep):
        return None, f"[error: invalid workspace path: {raw}]"

    return resolved, None


def workspace_write(path: str, content: str) -> str:
    """Write *content* to ``/data/workspace/<path>``.

    Creates parent directories if needed.

    Args:
        path: Path relative to workspace base.
        content: Text content to write.

    Returns:
        Full path written, or error string starting with '[error:'.
    """
    full, path_error = _resolve_workspace_path(path)
    if path_error is not None:
        return path_error

    base = WORKSPACE_BASE
    if not os.path.isdir("/data"):
        return "[error: no volume mounted at /data]"
    os.makedirs(base, exist_ok=True)
    if full is None:
        return "[error: invalid workspace path]"
    os.makedirs(os.path.dirname(full) or base, exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)
    return full


def workspace_read(path: str) -> str:
    """Read text from ``/data/workspace/<path>``.

    Args:
        path: Path relative to workspace base.

    Returns:
        File contents or error string starting with '[error:'.
    """
    full, path_error = _resolve_workspace_path(path)
    if path_error is not None:
        return path_error
    if full is None:
        return "[error: invalid workspace path]"
    if not os.path.isfile(full):
        return f"[error: file not found: {full}]"
    with open(full, encoding="utf-8") as fh:
        return fh.read()


def workspace_list(pattern: str = "*") -> list[str]:
    """List files in workspace matching glob *pattern*.

    Args:
        pattern: Glob pattern to match (default: "*" for all files).

    Returns:
        List of relative paths (relative to workspace base).
    """
    base = WORKSPACE_BASE
    if not os.path.isdir(base):
        return []
    search_path = os.path.join(base, "**", pattern)
    files = glob.glob(search_path, recursive=True)
    base_real = os.path.realpath(base)

    rel_paths: list[str] = []
    for found in files:
        if not os.path.isfile(found):
            continue
        found_real = os.path.realpath(found)
        if found_real != base_real and not found_real.startswith(base_real + os.sep):
            continue
        rel_paths.append(os.fsdecode(os.path.relpath(found_real, base_real)))
    return rel_paths


def workspace_append(path: str, content: str) -> str:
    """Append *content* to ``/data/workspace/<path>`` (creates if missing).

    Args:
        path: Path relative to workspace base.
        content: Text content to append.

    Returns:
        Full path written, or error string starting with '[error:'.
    """
    full, path_error = _resolve_workspace_path(path)
    if path_error is not None:
        return path_error

    base = WORKSPACE_BASE
    if not os.path.isdir("/data"):
        return "[error: no volume mounted at /data]"
    os.makedirs(base, exist_ok=True)
    if full is None:
        return "[error: invalid workspace path]"
    os.makedirs(os.path.dirname(full) or base, exist_ok=True)
    with open(full, "a", encoding="utf-8") as fh:
        fh.write(content)
    return full
