"""Document chunking strategies for long-context RLM workflows.

Provides pure functions for splitting large documents into manageable chunks.
These functions are designed to be:

    1. Self-contained (stdlib-only) so they can be injected into the sandbox
    2. Importable host-side for tests and notebooks
    3. Usable by the LLM inside the dspy.RLM REPL loop

Chunking strategies:
    - chunk_by_size: Fixed-size chunking with optional overlap
    - chunk_by_headers: Split markdown/structured text by header boundaries
"""

from __future__ import annotations

import re

# Pre-compiled regexes for default chunking patterns to avoid recompilation
# on every function call.
_DEFAULT_HEADER_PATTERN = re.compile(r"^#{1,3} ", re.MULTILINE)

# ═══════════════════════════════════════════════════════════════════════
# Fixed-size chunking
# ═══════════════════════════════════════════════════════════════════════


def chunk_by_size(
    text: str,
    size: int = 200_000,
    overlap: int = 0,
) -> list[str]:
    """Split text into fixed-size chunks with optional overlap.

    Args:
        text: The text to split.
        size: Maximum characters per chunk. Default: 200,000.
        overlap: Number of overlapping characters between consecutive
            chunks. Default: 0.

    Returns:
        List of text chunks. Empty list if text is empty.

    Example:
        >>> chunks = chunk_by_size("abcdefghij", size=4, overlap=1)
        >>> chunks
        ['abcd', 'defg', 'ghij']
    """
    if not text:
        return []
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0:
        raise ValueError("overlap must be non-negative")
    if overlap >= size:
        raise ValueError("overlap must be less than size")

    chunks: list[str] = []
    step = size - overlap
    for start in range(0, len(text), step):
        chunk = text[start : start + size]
        if chunk:
            chunks.append(chunk)
        if start + size >= len(text):
            break
    return chunks


# ═══════════════════════════════════════════════════════════════════════
# Header-based chunking
# ═══════════════════════════════════════════════════════════════════════


def chunk_by_headers(
    text: str,
    pattern: str | re.Pattern[str] | None = None,
    flags: int = re.MULTILINE,
) -> list[dict]:
    """Split text by header boundaries (markdown-style).

    Splits a document at lines matching the given header pattern.
    Each chunk includes the header line and all content until the
    next header or end of document.

    Args:
        text: The text to split.
        pattern: Regex pattern matching header lines. Accepts a pre-compiled
            ``re.Pattern`` or a string. Default: ``r"^#{1,3} "`` (markdown H1-H3).
        flags: Regex flags. Default: ``re.MULTILINE``.

    Returns:
        List of dicts with keys:
            - ``header``: The header line text (or "" for preamble)
            - ``content``: The content under that header
            - ``start_pos``: Character offset in original text

    Example:
        >>> text = "# Intro\\nHello\\n## Details\\nWorld"
        >>> chunks = chunk_by_headers(text)
        >>> chunks[0]["header"]
        '# Intro'
    """
    if not text:
        return []

    if isinstance(pattern, re.Pattern):
        compiled = pattern
    elif pattern is None:
        compiled = _DEFAULT_HEADER_PATTERN
    else:
        compiled = re.compile(pattern, flags)
    matches = list(compiled.finditer(text))

    if not matches:
        return [{"header": "", "content": text.strip(), "start_pos": 0}]

    chunks: list[dict] = []

    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            chunks.append({"header": "", "content": preamble, "start_pos": 0})

    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[match.start() : end]

        newline_pos = section.find("\n")
        if newline_pos == -1:
            header = section.strip()
            content = ""
        else:
            header = section[:newline_pos].strip()
            content = section[newline_pos + 1 :].strip()

        chunks.append({"header": header, "content": content, "start_pos": match.start()})

    return chunks


__all__ = [
    "chunk_by_size",
    "chunk_by_headers",
]
