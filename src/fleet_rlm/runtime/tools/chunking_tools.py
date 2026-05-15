"""Simplified document-chunking tool for the fleet tool registry.

Exports a module-level ``chunk_document`` function marked with ``@tool_fn``
so that ``discover_tools()`` can collect it.
"""

from __future__ import annotations

import re
from typing import Any

from fleet_rlm.runtime.content.chunking import (
    chunk_by_headers,
    chunk_by_json_keys,
    chunk_by_size,
    chunk_by_timestamps,
)
from fleet_rlm.runtime.tools._marker import tool_fn


def _normalize_strategy(strategy: str) -> str:
    """Normalise a chunking strategy name to its canonical form."""
    normalized = strategy.strip().lower().replace("-", "_")
    mapping = {
        "size": "size",
        "headers": "headers",
        "header": "headers",
        "timestamps": "timestamps",
        "timestamp": "timestamps",
        "json": "json_keys",
        "json_keys": "json_keys",
    }
    if normalized not in mapping:
        raise ValueError("Unsupported strategy. Choose one of: size, headers, timestamps, json_keys")
    return mapping[normalized]


def _chunk_text(
    text: str,
    strategy: str,
    *,
    size: int,
    overlap: int,
    pattern: str,
) -> list[Any]:
    """Chunk *text* using the named strategy."""
    strategy_norm = _normalize_strategy(strategy)
    if strategy_norm == "size":
        return chunk_by_size(text, size=size, overlap=overlap)
    if strategy_norm == "headers":
        return chunk_by_headers(text, pattern=pattern or r"^#{1,3} ")
    if strategy_norm == "timestamps":
        return chunk_by_timestamps(text, pattern=pattern or r"^\d{4}-\d{2}-\d{2}[T ]")
    return chunk_by_json_keys(text)


def _looks_like_document_alias(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 128:
        return False
    if "\n" in stripped or "\r" in stripped:
        return False
    if " " in stripped or "\t" in stripped:
        return False
    if stripped.startswith(("http://", "https://", "/", "./", "../")):
        return True
    if re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:csv|html?|json|md|pdf|txt|xml|ya?ml)", stripped):
        return True
    return bool(re.fullmatch(r"[a-z][a-z0-9]*(?:[_-][a-z0-9]+)+", stripped))


@tool_fn
def chunk_document(
    text: str,
    strategy: str = "size",
    size: int = 200_000,
    overlap: int = 0,
    pattern: str = "",
) -> dict[str, Any]:
    """Split document text into chunks for downstream processing."""
    if _looks_like_document_alias(text):
        return {
            "status": "warning",
            "reason": "alias_like_input",
            "strategy": _normalize_strategy(strategy),
            "chunk_count": 0,
            "preview": "",
            "warning": (
                "chunk_document received an alias/path-like token instead of "
                "document text. Load or fetch the document content first, then "
                "pass the actual text to chunk_document."
            ),
        }
    chunks = _chunk_text(text, strategy, size=size, overlap=overlap, pattern=pattern)
    preview = chunks[0] if chunks else ""
    return {
        "status": "ok",
        "strategy": _normalize_strategy(strategy),
        "chunk_count": len(chunks),
        "preview": str(preview)[:400],
    }


__all__ = ["chunk_document"]
