"""Document chunking strategies for long-context RLM workflows.

This module provides pure functions for splitting large documents into
manageable chunks. These functions are designed to be:

    1. Self-contained (stdlib-only) so they can be injected into the sandbox
    2. Importable host-side for tests and notebooks
    3. Usable by the LLM inside the dspy.RLM REPL loop

Chunking strategies:
    - chunk_by_size: Fixed-size chunking with optional overlap
    - chunk_by_headers: Split markdown/structured text by header boundaries
    - chunk_by_timestamps: Split log files by timestamp patterns
    - chunk_by_json_keys: Split JSON objects into per-key chunks

All functions use only the Python standard library (re, json) so they
can be serialized into the Modal sandbox environment.
"""

from __future__ import annotations

import json
import re


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
        # Stop if we've reached the end
        if start + size >= len(text):
            break
    return chunks


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


def chunk_by_json_keys(text: str) -> list[dict]:
    """Split a JSON object into per-key chunks.

    Parses the text as JSON and creates one chunk per top-level key.
    Useful for exploring large JSON configurations or API responses.

    Args:
        text: JSON string to split. Must be a JSON object (dict) at
            the top level.

    Returns:
        List of dicts with keys:
            - ``key``: The top-level JSON key
            - ``content``: JSON-serialized value for that key
            - ``value_type``: Python type name of the value

    Raises:
        ValueError: If text is not valid JSON or not a JSON object.

    Example:
        >>> text = '{"users": [1,2], "config": {"debug": true}}'
        >>> chunks = chunk_by_json_keys(text)
        >>> chunks[0]["key"]
        'users'
    """
    if not text or not text.strip():
        return []

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    chunks: list[dict] = []
    for key, value in data.items():
        chunks.append(
            {
                "key": key,
                "content": json.dumps(value, indent=2, default=str),
                "value_type": type(value).__name__,
            }
        )

    return chunks
