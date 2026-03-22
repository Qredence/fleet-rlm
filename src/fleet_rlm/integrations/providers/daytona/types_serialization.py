"""Shared serialization and normalization helpers for Daytona type payloads."""

from __future__ import annotations

import re
from typing import Any


_WHITESPACE_RE = re.compile(r"\s+")


_PROMPT_PREVIEW_LIMIT = 240


_PERSISTED_TEXT_LIMIT = 1_200


def _normalize_optional_text(value: Any, *, limit: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value)
    collapsed = _WHITESPACE_RE.sub(" ", text).strip()
    if not collapsed:
        return None
    if limit is not None and len(collapsed) > limit:
        return collapsed[:limit].rstrip()
    return collapsed


def _coerce_positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _coerce_nonnegative_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _persisted_text_preview(value: str, *, limit: int = _PERSISTED_TEXT_LIMIT) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n\n[truncated persisted preview]"


__all__ = [
    "_WHITESPACE_RE",
    "_PROMPT_PREVIEW_LIMIT",
    "_PERSISTED_TEXT_LIMIT",
    "_normalize_optional_text",
    "_coerce_positive_int",
    "_coerce_nonnegative_int",
    "_persisted_text_preview",
]
