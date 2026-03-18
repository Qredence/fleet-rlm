"""Header-based chunking strategy for markdown/structured text."""

from __future__ import annotations

import re


def chunk_by_headers(
    text: str,
    pattern: str = r"^#{1,3} ",
    flags: int = re.MULTILINE,
) -> list[dict]:
    """Split text by header boundaries (markdown-style).

    Splits a document at lines matching the given header pattern.
    Each chunk includes the header line and all content until the
    next header or end of document.

    Args:
        text: The text to split.
        pattern: Regex pattern matching header lines.
            Default: ``r"^#{1,3} "`` (markdown H1-H3).
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

    compiled = re.compile(pattern, flags)
    matches = list(compiled.finditer(text))

    if not matches:
        return [{"header": "", "content": text.strip(), "start_pos": 0}]

    chunks: list[dict] = []

    # Preamble before first header
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            chunks.append({"header": "", "content": preamble, "start_pos": 0})

    for i, match in enumerate(matches):
        # Get end position (start of next header or end of text)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[match.start() : end]

        # Split header from content
        newline_pos = section.find("\n")
        if newline_pos == -1:
            header = section.strip()
            content = ""
        else:
            header = section[:newline_pos].strip()
            content = section[newline_pos + 1 :].strip()

        chunks.append(
            {"header": header, "content": content, "start_pos": match.start()}
        )

    return chunks
