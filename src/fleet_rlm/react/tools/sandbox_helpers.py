"""Shared helper utilities for sandbox tools.

These utilities are extracted from tools_sandbox.py to improve testability
and reduce the file size while maintaining backward compatibility.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    """Coerce common model output variants into booleans safely.

    Args:
        value: Input value to coerce (bool, int, float, str, or None).
        default: Default value to return if coercion fails.

    Returns:
        Boolean representation of the input value.

    Examples:
        >>> _coerce_bool("yes")
        True
        >>> _coerce_bool("OFF")
        False
        >>> _coerce_bool(None)
        False
        >>> _coerce_bool(None, default=True)
        True
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return default


def _coerce_int(
    value: Any,
    *,
    default: int = 0,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    """Coerce model outputs into bounded ints with a safe fallback.

    Args:
        value: Input value to coerce (int, float, str, or None).
        default: Default value if coercion fails or value is None.
        min_value: Optional minimum bound for the result.
        max_value: Optional maximum bound for the result.

    Returns:
        Integer representation of the input, bounded by min/max if provided.

    Examples:
        >>> _coerce_int("42")
        42
        >>> _coerce_int("invalid", default=10)
        10
        >>> _coerce_int(150, max_value=100)
        100
        >>> _coerce_int(-5, min_value=0)
        0
    """
    if value is None:
        parsed = default
    else:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default

    if min_value is not None:
        parsed = max(min_value, parsed)
    if max_value is not None:
        parsed = min(max_value, parsed)
    return parsed


def _resolve_volume_path(
    path: str,
    *,
    default_root: str = "/data/memory",
    allowed_root: str = "/data",
) -> str:
    """Resolve *path* to a normalized path inside the mounted Modal volume.

    This function validates and normalizes paths to ensure they stay within
    the allowed volume mount point, preventing directory traversal attacks.

    Args:
        path: The path to resolve (absolute or relative).
        default_root: The root directory for relative paths.
        allowed_root: The top-level directory that all paths must stay within.

    Returns:
        Normalized absolute path within the allowed root.

    Raises:
        ValueError: If path is empty or escapes the allowed root.

    Examples:
        >>> _resolve_volume_path("my_file.txt")
        '/data/memory/my_file.txt'
        >>> _resolve_volume_path("/data/workspace/test.txt", default_root="/data/workspace")
        '/data/workspace/test.txt'
        >>> _resolve_volume_path("../../../etc/passwd")
        Traceback (most recent call last):
            ...
        ValueError: Path must stay within mounted volume root...
    """
    import posixpath

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
