"""Shared text-processing helpers."""

from __future__ import annotations

from typing import Any

_DEFAULT_COMPACT_LIMIT = 600


def compact_text(value: Any, *, limit: int = _DEFAULT_COMPACT_LIMIT) -> str:
    """Return *value* as a string truncated to *limit* characters."""
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
