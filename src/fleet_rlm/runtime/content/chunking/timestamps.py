"""Timestamp-based chunking strategy for log files."""

from __future__ import annotations

import re


def chunk_by_timestamps(
    text: str,
    pattern: str = r"^\d{4}-\d{2}-\d{2}[T ]",
    flags: int = re.MULTILINE,
) -> list[dict]:
    """Split log-style text by timestamp boundaries.

    Splits text at lines starting with a timestamp pattern. Each chunk
    contains all log entries from one timestamp boundary to the next.

    Args:
        text: The log text to split.
        pattern: Regex pattern matching timestamp line starts.
            Default: ISO-8601 style ``r"^\\d{4}-\\d{2}-\\d{2}[T ]"``.
        flags: Regex flags. Default: ``re.MULTILINE``.

    Returns:
        List of dicts with keys:
            - ``timestamp``: The matched timestamp prefix
            - ``content``: Full log entry/entries for this boundary
            - ``start_pos``: Character offset in original text

    Example:
        >>> logs = "2026-01-01 INFO Start\\n2026-01-02 ERROR Fail"
        >>> chunks = chunk_by_timestamps(logs)
        >>> len(chunks)
        2
    """
    if not text:
        return []

    compiled = re.compile(pattern, flags)
    matches = list(compiled.finditer(text))

    if not matches:
        return [{"timestamp": "", "content": text, "start_pos": 0}]

    chunks: list[dict] = []

    # Content before first timestamp
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            chunks.append({"timestamp": "", "content": preamble, "start_pos": 0})

    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[match.start() : end].strip()
        timestamp = match.group(0).strip()
        chunks.append(
            {"timestamp": timestamp, "content": content, "start_pos": match.start()}
        )

    return chunks
