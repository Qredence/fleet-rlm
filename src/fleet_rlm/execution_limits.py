"""Shared execution payload limit helpers for runtime and observability paths."""

from __future__ import annotations

import os

DEFAULT_MAX_TEXT_CHARS = 65536
DEFAULT_MAX_COLLECTION_ITEMS = 500
DEFAULT_MAX_RECURSION_DEPTH = 12
MAX_ENV_LIMIT = 1_000_000


def env_positive_int(name: str, default: int) -> int:
    """Read a positive integer env var with bounds and safe fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return min(parsed, MAX_ENV_LIMIT)


def execution_max_text_chars() -> int:
    return env_positive_int("WS_EXECUTION_MAX_TEXT_CHARS", DEFAULT_MAX_TEXT_CHARS)


def execution_max_collection_items() -> int:
    return env_positive_int(
        "WS_EXECUTION_MAX_COLLECTION_ITEMS", DEFAULT_MAX_COLLECTION_ITEMS
    )


def execution_max_recursion_depth() -> int:
    return env_positive_int(
        "WS_EXECUTION_MAX_RECURSION_DEPTH", DEFAULT_MAX_RECURSION_DEPTH
    )
