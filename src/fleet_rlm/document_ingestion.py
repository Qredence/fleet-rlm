"""Shared document ingestion helpers for host-side file analysis."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# File suffixes supported by MarkItDown for document conversion
MARKITDOWN_SUFFIXES = {
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


def extract_text_with_markitdown(path: Path) -> tuple[str, dict[str, Any]]:
    """Extract document text via MarkItDown."""

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


def extract_text_with_pypdf(path: Path) -> tuple[str, dict[str, Any]]:
    """Extract PDF text with pypdf as fallback."""

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


def looks_like_binary(path: Path, probe_bytes: int = 2048) -> bool:
    """Heuristic for binary files to avoid UTF-8 decoding crashes."""

    sample = path.read_bytes()[:probe_bytes]
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    textish = b"\n\r\t\f\b"
    printable = sum((32 <= byte <= 126) or byte in textish for byte in sample)
    return printable / len(sample) < 0.8


def read_document_content(path: Path) -> tuple[str, dict[str, Any]]:
    """Read document content with safe handling for PDF/binary formats."""

    suffix = path.suffix.lower()

    if suffix == ".pdf":
        extraction_errors: list[str] = []
        try:
            text, meta = extract_text_with_markitdown(path)
            if text.strip():
                return text, meta
            extraction_errors.append("markitdown returned no text")
        except Exception as exc:  # pragma: no cover - exercised via tests
            extraction_errors.append(f"markitdown: {exc}")

        try:
            text, meta = extract_text_with_pypdf(path)
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

    if suffix in MARKITDOWN_SUFFIXES:
        try:
            text, meta = extract_text_with_markitdown(path)
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
        return path.read_text(encoding="utf-8"), {
            "source_type": "text",
            "extraction_method": "read_text",
        }
    except UnicodeDecodeError as exc:
        if looks_like_binary(path):
            raise ValueError(
                f"Binary file detected at '{path}'. Use a text file or supported document format (for example, PDF)."
            ) from exc
        raise ValueError(f"Could not decode '{path}' as UTF-8 text.") from exc
