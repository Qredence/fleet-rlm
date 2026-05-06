"""Shared path and tree helpers for persistent runtime storage."""

from __future__ import annotations

import hashlib
import os
from pathlib import PurePosixPath


def entry_name(entry_path: str) -> str:
    return PurePosixPath(entry_path.rstrip("/")).name


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

    joined = os.path.normpath(raw) if os.path.isabs(raw) else os.path.normpath(os.path.join(root, raw))
    resolved = os.path.realpath(joined)
    if resolved != root_real and not resolved.startswith(root_real + os.sep):
        return None, f"{invalid_error_prefix}{raw}"
    return resolved, None
