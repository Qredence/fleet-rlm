"""DSPy ReAct document tools for the RLM chat agent.

This module contains document management tools that handle loading, caching,
and managing documents for the ReAct agent. Tools are defined as standalone
functions following the DSPy convention.

Tools included:
- load_document: Load a text document from host filesystem or public URL into agent memory
- set_active_document: Set which loaded document alias should be used by default
- list_documents: List loaded document aliases and active document metadata
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fleet_rlm.core.execution.document_sources import (
    fetch_url_document_content,
    is_http_url,
)
from fleet_rlm.features.document_ingestion.main import (
    read_document_content as _read_document_content,
)

if TYPE_CHECKING:
    from ..chat_agent import RLMReActChatAgent


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_document_tools(agent: RLMReActChatAgent) -> list[Any]:
    """Build document management tools with closures bound to *agent*.

    Each inner function has a descriptive ``__name__``, docstring, and
    type-hinted parameters so ``dspy.ReAct`` can introspect them cleanly.

    Args:
        agent: The RLMReActChatAgent instance to bind tools to.

    Returns:
        List of dspy.Tool objects for document management.
    """
    from dspy import Tool

    def _load_document_impl(path: str, alias: str = "active") -> dict[str, Any]:
        """Shared implementation for loading local or URL-backed documents."""
        if is_http_url(path):
            content, metadata = fetch_url_document_content(
                path, read_document_content=_read_document_content
            )
            agent._set_document(alias, content)
            agent.active_alias = alias
            response = {
                "status": "ok",
                "alias": alias,
                "path": path,
                "chars": len(content),
                "lines": len(content.splitlines()),
            }
            response.update(metadata)
            return response

        docs_path = Path(path)
        if not docs_path.exists():
            raise FileNotFoundError(f"Document not found: {docs_path}")

        # Handle directory: return file listing
        if docs_path.is_dir():
            # Make paths relative to cwd for easy reuse in load_document
            cwd = Path.cwd()
            files = sorted(
                str(p.relative_to(cwd) if p.is_relative_to(cwd) else p)
                for p in docs_path.rglob("*")
                if p.is_file()
            )
            return {
                "status": "directory",
                "path": str(docs_path),
                "files": files[:100],  # Cap at 100 for display
                "total_count": len(files),
                "hint": "Use load_document with a specific file path from this listing.",
            }

        # Handle file: load content
        content, metadata = _read_document_content(docs_path)
        agent._set_document(alias, content)
        agent.active_alias = alias
        response = {
            "status": "ok",
            "alias": alias,
            "path": str(docs_path),
            "chars": len(content),
            "lines": len(content.splitlines()),
        }
        if metadata.get("source_type") != "text":
            response.update(metadata)
        return response

    def load_document(path: str, alias: str = "active") -> dict[str, Any]:
        """Load a text document from host filesystem or public URL into agent memory.

        If path is a directory, returns a recursive file listing instead of loading.

        Args:
            path: File path, directory path, or public HTTP(S) URL.
            alias: Optional alias to reference this document later. Defaults to "active".

        Returns:
            Dictionary with status, path, character count, and line count for files.
            For directories, returns file listing with total count.
        """
        return _load_document_impl(path, alias=alias)

    def fetch_web_document(url: str, alias: str = "active") -> dict[str, Any]:
        """Fetch and load a document from a public HTTP(S) URL into agent memory.

        This is a thin alias over ``load_document`` URL support and exists for
        discoverability in tool lists.

        Args:
            url: Public HTTP(S) URL to fetch and ingest.
            alias: Optional alias to reference this document later. Defaults to "active".

        Returns:
            Same dictionary payload as ``load_document`` for URL-backed documents.
        """
        return _load_document_impl(url, alias=alias)

    def set_active_document(alias: str) -> dict[str, Any]:
        """Set which loaded document alias should be used by default tools.

        Args:
            alias: The document alias to set as active.

        Returns:
            Dictionary with status and the new active alias.

        Raises:
            ValueError: If the alias is not found in loaded documents.
        """
        if alias not in agent.documents:
            raise ValueError(f"Unknown document alias: {alias}")
        agent.active_alias = alias
        return {"status": "ok", "active_alias": alias}

    def list_documents() -> dict[str, Any]:
        """List loaded document aliases and active document metadata.

        Returns:
            Dictionary with list of documents, active alias, and cache info.
        """
        docs = []
        for doc_alias, text in agent._document_cache.items():
            docs.append(
                {
                    "alias": doc_alias,
                    "chars": len(text),
                    "lines": len(text.splitlines()),
                }
            )
        return {
            "documents": docs,
            "active_alias": agent.active_alias,
            "cache_size": len(agent._document_cache),
            "cache_limit": agent._max_documents,
        }

    return [
        Tool(
            load_document,
            name="load_document",
            desc="Load a text document from host filesystem or public HTTP(S) URL into agent document memory",
        ),
        Tool(
            fetch_web_document,
            name="fetch_web_document",
            desc="Fetch and load a document from a public HTTP(S) URL into agent document memory",
        ),
        Tool(
            set_active_document,
            name="set_active_document",
            desc="Set which loaded document alias should be used by default tools",
        ),
        Tool(
            list_documents,
            name="list_documents",
            desc="List loaded document aliases and active document metadata",
        ),
    ]
