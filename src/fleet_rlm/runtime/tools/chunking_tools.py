"""Simplified document-chunking tool for the fleet tool registry.

Exports a module-level ``chunk_document`` function marked with ``@tool_fn``
so that ``discover_tools()`` can collect it.
"""

from __future__ import annotations

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
        raise ValueError(
            "Unsupported strategy. Choose one of: size, headers, timestamps, json_keys"
        )
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


@tool_fn
def chunk_document(
    text: str,
    strategy: str = "size",
    size: int = 200_000,
    overlap: int = 0,
    pattern: str = "",
) -> dict[str, Any]:
    """Split a text document into chunks using the specified strategy.

    Supported strategies:

    * ``size`` — fixed character-count windows with optional overlap.
    * ``headers`` — split on Markdown heading patterns.
    * ``timestamps`` — split on ISO-8601 timestamp prefixes.
    * ``json_keys`` — split a JSON document on top-level keys.

    Args:
        text: The source text to chunk.
        strategy: Chunking strategy name. Defaults to ``"size"``.
        size: Chunk size in characters for the ``size`` strategy. Defaults
            to 200 000.
        overlap: Overlap in characters between adjacent chunks for the
            ``size`` strategy. Defaults to 0.
        pattern: Custom regex delimiter for ``headers`` or ``timestamps``
            strategies.  Ignored for other strategies.

    Returns:
        Dictionary with ``status``, ``strategy``, ``chunk_count``, and a
        ``preview`` of the first chunk.
    """
    chunks = _chunk_text(text, strategy, size=size, overlap=overlap, pattern=pattern)
    preview = chunks[0] if chunks else ""
    return {
        "status": "ok",
        "strategy": _normalize_strategy(strategy),
        "chunk_count": len(chunks),
        "preview": str(preview)[:400],
    }


__all__ = ["chunk_document"]
