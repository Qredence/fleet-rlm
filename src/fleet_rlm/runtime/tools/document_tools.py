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
import ipaddress
import socket
import tempfile
import urllib.parse
import urllib.request
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Any

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


def _is_private_download_address(address: str) -> bool:
    """Return whether an IP address should be blocked for bridged downloads."""
    ip = ipaddress.ip_address(address)
    return any(
        (
            ip.is_loopback,
            ip.is_private,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def _validate_download_url(url: str) -> None:
    """Reject download URLs that target local or private network addresses."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Document downloads only support HTTP(S) URLs.")
    if not parsed.hostname:
        raise ValueError("Document download URL must include a hostname.")

    hostname = parsed.hostname.strip().rstrip(".")
    if hostname.lower() == "localhost":
        raise ValueError("Document download URL targets a private network address.")

    try:
        if _is_private_download_address(hostname):
            raise ValueError("Document download URL targets a private network address.")
        return
    except ValueError as exc:
        if "private network" in str(exc):
            raise

    try:
        resolved = socket.getaddrinfo(hostname, parsed.port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(
            f"Unable to resolve document download host: {hostname}"
        ) from exc

    for result in resolved:
        sockaddr = result[4]
        if sockaddr and _is_private_download_address(str(sockaddr[0])):
            raise ValueError("Document download URL targets a private network address.")


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate each redirect target before urllib follows it."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        safe_url = urllib.parse.urljoin(req.full_url, newurl)
        _validate_download_url(safe_url)
        return super().redirect_request(req, fp, code, msg, headers, safe_url)


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
    _validate_download_url(url)
    opener = urllib.request.build_opener(_ValidatingRedirectHandler())
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "fleet-rlm/1.0"},
        method="GET",
    )
    with opener.open(req, timeout=_DOWNLOAD_TIMEOUT_S) as response:  # noqa: S310
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
    """Report that standalone document alias activation is unsupported.

    The agent-bound version provided by ``AgentRuntime`` updates the agent's
    document cache. The standalone tool has no cache state to mutate, so it
    fails explicitly instead of reporting a false-positive active alias.

    Args:
        alias: The document alias to set as active.

    Returns:
        Dictionary with ``status`` and an explanatory ``error``.
    """
    return {
        "status": "error",
        "active_alias": alias,
        "error": "set_active_document is unavailable without an agent document cache.",
    }


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
    """Fetch and extract text from an HTTP(S) document URL.

    Unlike :func:`load_document`, this function returns the raw document text
    directly so it can be used as a bridged sandbox tool — the sandbox RLM can
    call ``fetch_document_text(url)`` and immediately process the returned text
    without any host-side document caching.

    Supports the same remote formats as :func:`load_document` (PDF via
    MarkItDown, plain text, HTML, Office documents, etc.) and the same 50 MiB
    download cap.

    Args:
        url_or_path: HTTP(S) URL.

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
        if not stripped.startswith(("http://", "https://")):
            return {
                "status": "error",
                "error": "fetch_document_text only accepts HTTP(S) URLs.",
            }
        tmp_path = _download_url(stripped)
        file_path = tmp_path

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
