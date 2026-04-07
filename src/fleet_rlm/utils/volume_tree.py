"""Shared path and tree helpers for persistent runtime storage."""

from __future__ import annotations

import hashlib
import os
import posixpath
from pathlib import PurePosixPath


def entry_name(entry_path: str) -> str:
    raw = entry_path.rstrip("/")
    return raw.rsplit("/", 1)[-1] if "/" in raw else raw


def stable_tree_id(path: str) -> str:
    return hashlib.sha256(path.encode()).hexdigest()[:12]


def resolve_realpath_within_root(
    path: str,
    *,
    root: str,
    empty_error: str,
    invalid_error_prefix: str,
) -> tuple[str | None, str | None]:
    root_real = os.path.realpath(root)
    raw = str(path or "").strip()
    if not raw:
        return None, empty_error

    joined = (
        os.path.normpath(raw)
        if os.path.isabs(raw)
        else os.path.normpath(os.path.join(root, raw))
    )
    resolved = os.path.realpath(joined)
    if resolved != root_real and not resolved.startswith(root_real + os.sep):
        return None, f"{invalid_error_prefix}{raw}"
    return resolved, None


def resolve_mounted_volume_path(
    path: str,
    *,
    default_root: str = "/home/daytona/memory",
    allowed_root: str = "/home/daytona/memory",
) -> str:
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("Path cannot be empty.")

    allowed = str(PurePosixPath(allowed_root))
    default = str(PurePosixPath(default_root))
    candidate = PurePosixPath(raw)

    normalized = str(
        candidate if candidate.is_absolute() else PurePosixPath(default) / candidate
    )
    normalized = posixpath.normpath(normalized)
    normalized = str(PurePosixPath(normalized))

    if normalized != allowed and not normalized.startswith(allowed + "/"):
        raise ValueError(
            f"Path must stay within mounted volume root '{allowed}'. Got: {path}"
        )
    return normalized
