"""Simplified document-loading tools for the fleet tool registry.

Exports module-level ``load_document``, ``set_active_document``, and
``list_documents`` functions marked with ``@tool_fn`` so that
``discover_tools()`` can collect them.

For full agent-bound behaviour (Daytona workspace path resolution, document
caching, URL fetching with size limits) use the builder in
``runtime/tools/content/document.py`` via ``AgentRuntime``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fleet_rlm.runtime.tools._marker import tool_fn


@tool_fn
def load_document(path: str, alias: str = "active") -> dict[str, Any]:
    """Load a text document from the host filesystem or a public URL.

    When *path* is a local directory, returns a recursive file listing
    instead of loading file content.  For agent-managed document caching
    and Daytona workspace path resolution, use the agent-bound version
    provided by ``AgentRuntime``.

    Args:
        path: Absolute or relative file path, directory path, or HTTP(S) URL.
        alias: Alias to store the document under. Defaults to ``"active"``.

    Returns:
        Dictionary with ``status``, ``path``, ``char_count``, and ``line_count``
        for files; ``status``, ``path``, and ``files`` for directories.
    """
    from fleet_rlm.runtime.content.ingestion import (
        read_document_content as _read_document_content,
    )

    file_path = Path(path)

    if file_path.is_dir():
        files = sorted(
            str(p.relative_to(file_path)) for p in file_path.rglob("*") if p.is_file()
        )
        return {
            "status": "directory",
            "alias": alias,
            "path": str(file_path),
            "files": files[:100],
            "total_count": len(files),
            "hint": "Use load_document with a specific file path from this listing.",
        }

    if not file_path.exists():
        raise FileNotFoundError(f"Document not found: {file_path}")

    content, metadata = _read_document_content(file_path)
    return {
        "status": "ok",
        "alias": alias,
        "path": str(file_path),
        "char_count": len(content),
        "line_count": len(content.splitlines()),
        "metadata": metadata or {},
    }


@tool_fn
def set_active_document(alias: str) -> dict[str, Any]:
    """Set the active document alias for subsequent tool calls.

    This standalone version is a no-op placeholder.  The agent-bound
    version provided by ``AgentRuntime`` updates the agent's document cache.

    Args:
        alias: The document alias to set as active.

    Returns:
        Dictionary with ``status`` and ``active_alias``.
    """
    return {"status": "ok", "active_alias": alias}


@tool_fn
def list_documents() -> dict[str, Any]:
    """List loaded document aliases and metadata.

    This standalone version always returns an empty cache.  The agent-bound
    version provided by ``AgentRuntime`` queries the agent's document cache.

    Returns:
        Dictionary with ``documents``, ``active_alias``, and ``cache_size``.
    """
    return {
        "documents": [],
        "active_alias": "active",
        "cache_size": 0,
    }


__all__ = ["list_documents", "load_document", "set_active_document"]
