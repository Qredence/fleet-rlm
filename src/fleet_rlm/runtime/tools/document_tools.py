"""Simplified document-loading tools for the fleet tool registry.

Exports module-level ``load_document``, ``set_active_document``, and
``list_documents`` functions marked with ``@tool_fn`` so that
``discover_tools()`` can collect them.

For full agent-bound behaviour (Daytona workspace path resolution, document
caching, URL fetching with size limits) use the builder in
``runtime/tools/content/document.py`` via ``AgentRuntime``.
"""

from __future__ import annotations

import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fleet_rlm.runtime.tools._marker import tool_fn

_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MiB
_DOWNLOAD_TIMEOUT_S = 30
_CONTENT_TYPE_SUFFIX_MAP = {
    "application/pdf": ".pdf",
    "text/html": ".html",
    "text/plain": ".txt",
    "application/json": ".json",
    "text/markdown": ".md",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


def _suffix_from_url(url: str, headers: dict[str, str]) -> str:
    """Derive a file suffix from URL path or Content-Type header."""
    parsed = urllib.parse.urlparse(url)
    url_suffix = Path(parsed.path).suffix.lower()
    if url_suffix and url_suffix not in {
        ".html",
        ".htm",
        ".txt",
        ".md",
        ".json",
        ".pdf",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
        ".rtf",
        ".epub",
    }:
        url_suffix = ""
    if url_suffix:
        return url_suffix

    content_type = headers.get("Content-Type", "").split(";")[0].strip().lower()
    return _CONTENT_TYPE_SUFFIX_MAP.get(content_type, ".txt")


def _download_url(url: str) -> Path:
    """Download *url* to a temporary file and return the path."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "fleet-rlm/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_S) as response:  # noqa: S310
        headers = dict(response.headers)
        suffix = _suffix_from_url(url, headers)

        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        cleanup_tmp = False
        try:
            try:
                downloaded = 0
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > _MAX_DOWNLOAD_BYTES:
                        raise ValueError(
                            f"Download from {url} exceeds {_MAX_DOWNLOAD_BYTES} byte limit."
                        )
                    os.write(fd, chunk)
            except Exception:
                cleanup_tmp = True
                raise
        finally:
            os.close(fd)
            if cleanup_tmp:
                Path(tmp_path).unlink(missing_ok=True)

    return Path(tmp_path)


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

    # Handle HTTP(S) URLs
    stripped = path.strip()
    if stripped.startswith(("http://", "https://")):
        tmp_path = _download_url(stripped)
        try:
            content, metadata = _read_document_content(tmp_path)
            return {
                "status": "ok",
                "alias": alias,
                "path": stripped,
                "char_count": len(content),
                "line_count": len(content.splitlines()),
                "metadata": metadata or {},
            }
        finally:
            tmp_path.unlink(missing_ok=True)

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


def fetch_document_text(url_or_path: str) -> dict[str, Any]:
    """Fetch and extract text from a URL or local file path.

    Unlike :func:`load_document`, this function returns the raw document text
    directly so it can be used as a bridged sandbox tool — the sandbox RLM can
    call ``fetch_document_text(url)`` and immediately process the returned text
    without any host-side document caching.

    Supports the same formats as :func:`load_document` (PDF via MarkItDown,
    plain text, HTML, Office documents, etc.) and the same 50 MiB download cap
    for URLs.

    Args:
        url_or_path: HTTP(S) URL or absolute/relative local file path.

    Returns:
        A dict with:
        - ``status``: ``"ok"`` on success, ``"error"`` on failure.
        - ``text``: Extracted document text (present when ``status == "ok"``).
        - ``char_count``: Length of extracted text in characters.
        - ``metadata``: Extraction metadata dict (source_type, extraction_method, etc.).
        - ``error``: Error message (present when ``status == "error"``).
    """
    from fleet_rlm.runtime.content.ingestion import (
        read_document_content as _read_document_content,
    )

    stripped = url_or_path.strip()
    tmp_path: Path | None = None
    try:
        if stripped.startswith(("http://", "https://")):
            tmp_path = _download_url(stripped)
            file_path = tmp_path
        else:
            file_path = Path(stripped)
            if not file_path.exists():
                return {"status": "error", "error": f"File not found: {stripped}"}

        text, metadata = _read_document_content(file_path)
        return {
            "status": "ok",
            "text": text,
            "char_count": len(text),
            "metadata": metadata or {},
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


__all__ = [
    "fetch_document_text",
    "list_documents",
    "load_document",
    "set_active_document",
]
