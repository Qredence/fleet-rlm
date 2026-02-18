"""DSPy ReAct document tools for the RLM chat agent.

This module contains document management tools that handle loading, caching,
and managing documents for the ReAct agent. Tools are defined as standalone
functions following the DSPy convention.

Tools included:
- load_document: Load a text document from host filesystem into agent memory
- set_active_document: Set which loaded document alias should be used by default
- list_documents: List loaded document aliases and active document metadata
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import RLMReActChatAgent

logger = logging.getLogger(__name__)

# File suffixes supported by MarkItDown for document conversion
_MARKITDOWN_SUFFIXES = {
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".rtf",
    ".epub",
    ".html",
    ".htm",
}


# ---------------------------------------------------------------------------
# Helper functions (not exposed to DSPy)
# ---------------------------------------------------------------------------


def _extract_text_with_markitdown(path: Path) -> tuple[str, dict[str, Any]]:
    """Extract document text via MarkItDown.

    Args:
        path: Path to the document file.

    Returns:
        Tuple of (extracted_text, metadata_dict).

    Raises:
        RuntimeError: If MarkItDown is not installed.
    """
    try:
        from markitdown import MarkItDown
    except ImportError as exc:
        raise RuntimeError(
            "MarkItDown is not installed. Run `uv sync` to install runtime dependencies."
        ) from exc

    converter = MarkItDown()
    converted = converter.convert(str(path))

    text_value = ""
    for attr in ("text_content", "markdown", "content", "text"):
        candidate = getattr(converted, attr, None)
        if isinstance(candidate, str) and candidate.strip():
            text_value = candidate
            break
    if not text_value and isinstance(converted, str):
        text_value = converted.strip()

    return (
        text_value,
        {
            "source_type": path.suffix.lower().lstrip(".") or "document",
            "extraction_method": "markitdown",
        },
    )


def _extract_text_with_pypdf(path: Path) -> tuple[str, dict[str, Any]]:
    """Extract PDF text with pypdf as fallback.

    Args:
        path: Path to the PDF file.

    Returns:
        Tuple of (extracted_text, metadata_dict) including page counts.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    page_texts: list[str] = []
    pages_with_text = 0
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        if text:
            pages_with_text += 1
        page_texts.append(text)

    return (
        "\n\n".join(page_texts).strip(),
        {
            "source_type": "pdf",
            "extraction_method": "pypdf",
            "page_count": len(reader.pages),
            "pages_with_text": pages_with_text,
        },
    )


def _looks_like_binary(path: Path, probe_bytes: int = 2048) -> bool:
    """Heuristic for binary files to avoid UTF-8 decoding crashes.

    Args:
        path: Path to the file to check.
        probe_bytes: Number of bytes to sample for binary detection.

    Returns:
        True if the file appears to be binary, False otherwise.
    """
    sample = path.read_bytes()[:probe_bytes]
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    textish = b"\n\r\t\f\b"
    printable = sum((32 <= byte <= 126) or byte in textish for byte in sample)
    return printable / len(sample) < 0.8


def _read_document_content(path: Path) -> tuple[str, dict[str, Any]]:
    """Read document content with safe handling for PDF/binary formats.

    Handles multiple document formats including PDF, Office documents,
    and plain text files. Falls back through extraction methods as needed.

    Args:
        path: Path to the document file.

    Returns:
        Tuple of (content_text, metadata_dict).

    Raises:
        ValueError: If the file cannot be read or is a binary format.
    """
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        extraction_errors: list[str] = []
        try:
            text, meta = _extract_text_with_markitdown(path)
            if text.strip():
                return text, meta
            extraction_errors.append("markitdown returned no text")
        except (
            Exception
        ) as exc:  # pragma: no cover - exercised via tests with monkeypatch
            extraction_errors.append(f"markitdown: {exc}")

        try:
            text, meta = _extract_text_with_pypdf(path)
            if text.strip():
                return text, meta
        except Exception as exc:
            extraction_errors.append(f"pypdf: {exc}")
            details = "; ".join(extraction_errors)
            raise ValueError(
                f"Could not extract text from PDF '{path}'. Details: {details}"
            ) from exc

        raise ValueError(
            f"PDF '{path}' appears to be image-only or scanned. OCR is required before analysis."
        )

    if suffix in _MARKITDOWN_SUFFIXES:
        try:
            text, meta = _extract_text_with_markitdown(path)
            if text.strip():
                return text, meta
        except Exception as exc:
            logger.warning(
                "markitdown extraction failed for '%s' (suffix %s); falling back to read_text: %s",
                path,
                suffix,
                exc,
            )

    try:
        return path.read_text(), {
            "source_type": "text",
            "extraction_method": "read_text",
        }
    except UnicodeDecodeError as exc:
        if _looks_like_binary(path):
            raise ValueError(
                f"Binary file detected at '{path}'. Use a text file or supported document format (for example, PDF)."
            ) from exc
        raise ValueError(f"Could not decode '{path}' as UTF-8 text.") from exc


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

    def load_document(path: str, alias: str = "active") -> dict[str, Any]:
        """Load a text document from host filesystem into agent document memory.

        If path is a directory, returns a recursive file listing instead of loading.

        Args:
            path: File path or directory path on the host filesystem.
            alias: Optional alias to reference this document later. Defaults to "active".

        Returns:
            Dictionary with status, path, character count, and line count for files.
            For directories, returns file listing with total count.
        """
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
            desc="Load a text document from host filesystem into agent document memory",
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
