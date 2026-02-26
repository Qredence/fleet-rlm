"""Compatibility memory tools.

The legacy evolutive-memory SQL tables were retired in v0.4.8 cleanup.
This module remains intentionally importable for one release cycle so tool
registration and call-sites do not break while callers migrate to Neon-backed
memory APIs.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def search_evolutive_memory(query: str) -> str:
    """Compatibility implementation for the retired legacy memory search tool."""
    safe_query = query.strip()
    logger.info(
        "search_evolutive_memory invoked in compatibility mode",
        extra={"query_chars": len(safe_query)},
    )
    prefix = safe_query[:160] if safe_query else "<empty>"
    return (
        "[LEGACY MEMORY DEPRECATED]\n"
        "The evolutive-memory SQL tables were removed in v0.4.8.\n"
        "No legacy memory records are available from this tool.\n"
        f"Query received: {prefix}"
    )


__all__ = ["search_evolutive_memory"]
