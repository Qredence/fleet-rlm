"""Document cache management mixin for ReAct agent.

This module provides LRU (Least Recently Used) caching for documents
to prevent unbounded memory growth while maintaining fast access to
frequently used documents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass  # No imports needed for type hints here


class DocumentCacheMixin:
    """Mixin providing LRU document caching for ReAct agents.

    Manages a cache of documents with LRU eviction to prevent unbounded
    memory growth. Documents are accessed by alias and tracked for
    recency of use.

    Attributes:
        _document_cache: Dict mapping aliases to document content
        _document_access_order: List tracking LRU order (most recent at end)
        _max_documents: Maximum number of documents to cache
        active_alias: Currently active document alias
    """

    # Default maximum documents in cache
    _DEFAULT_MAX_DOCUMENTS: int = 100

    def _init_document_cache(self) -> None:
        """Initialize document cache structures.

        Called during __init__ to set up the cache structures.
        """
        self._document_cache: dict[str, str] = {}
        self._document_access_order: list[str] = []
        self._max_documents: int = self._DEFAULT_MAX_DOCUMENTS
        self.active_alias: str | None = None

    def _get_document(self, alias: str) -> str:
        """Get document with LRU cache tracking.

        Args:
            alias: The document alias to retrieve

        Returns:
            The document content

        Raises:
            KeyError: If the alias is not found in the cache
        """
        if alias not in self._document_cache:
            raise KeyError(f"Document alias '{alias}' not found")
        if alias in self._document_access_order:
            self._document_access_order.remove(alias)
        self._document_access_order.append(alias)
        return self._document_cache[alias]

    def _set_document(self, alias: str, content: str) -> None:
        """Set document with LRU eviction if needed.

        Args:
            alias: The document alias
            content: The document content
        """
        if (
            alias not in self._document_cache
            and len(self._document_cache) >= self._max_documents
        ):
            oldest = self._document_access_order.pop(0)
            del self._document_cache[oldest]
        self._document_cache[alias] = content
        if alias in self._document_access_order:
            self._document_access_order.remove(alias)
        self._document_access_order.append(alias)

    def _delete_document(self, alias: str) -> None:
        """Delete document from cache.

        Args:
            alias: The document alias to delete
        """
        if alias in self._document_cache:
            del self._document_cache[alias]
        if alias in self._document_access_order:
            self._document_access_order.remove(alias)

    @property
    def documents(self) -> dict[str, str]:
        """Backward-compatible document access.

        Returns:
            Copy of the document cache dict
        """
        return self._document_cache

    def clear_document_cache(self) -> int:
        """Clear all documents from cache.

        Returns:
            Number of documents that were cleared
        """
        count = len(self._document_cache)
        self._document_cache.clear()
        self._document_access_order.clear()
        self.active_alias = None
        return count

    def get_document_cache_state(self) -> dict[str, Any]:
        """Export document cache state for persistence.

        Returns:
            Dict with cache state including documents, order, and active alias
        """
        return {
            "documents": dict(self._document_cache),
            "document_access_order": list(self._document_access_order),
            "active_alias": self.active_alias,
        }

    def restore_document_cache_state(self, state: dict[str, Any]) -> None:
        """Restore document cache from a previously exported state.

        Args:
            state: Dict with cache state from get_document_cache_state()
        """
        documents = state.get("documents", {})
        if isinstance(documents, dict):
            self._document_cache = {
                str(alias): str(content) for alias, content in documents.items()
            }
        else:
            self._document_cache = {}

        access_order = state.get("document_access_order", [])
        if isinstance(access_order, list):
            self._document_access_order = [
                str(alias)
                for alias in access_order
                if str(alias) in self._document_cache
            ]
        else:
            self._document_access_order = []

        # Ensure all cached docs appear in order, even if absent in saved LRU list
        for alias in self._document_cache:
            if alias not in self._document_access_order:
                self._document_access_order.append(alias)

        active_alias = state.get("active_alias")
        if isinstance(active_alias, str) and active_alias in self._document_cache:
            self.active_alias = active_alias
        else:
            self.active_alias = None
