"""Simplified document-chunking tool for the fleet tool registry.

Exports a module-level ``chunk_document`` function marked with ``@tool_fn``
so that ``discover_tools()`` can collect it.

For the full agent-bound experience (host and sandbox chunking with buffer
storage), use the builders in ``runtime/tools/content/chunking.py`` via
``AgentRuntime``.
"""

from __future__ import annotations

from typing import Any

from fleet_rlm.runtime.tools._marker import tool_fn


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
    from fleet_rlm.runtime.tools.shared import chunk_text, normalize_strategy

    chunks = chunk_text(text, strategy, size=size, overlap=overlap, pattern=pattern)
    preview = chunks[0] if chunks else ""
    return {
        "status": "ok",
        "strategy": normalize_strategy(strategy),
        "chunk_count": len(chunks),
        "preview": str(preview)[:400],
    }


__all__ = ["chunk_document"]
