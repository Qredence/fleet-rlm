"""Sandbox text processing and chunking tools.

These helper functions are injected into sandbox_globals so LLM-generated
code can call them directly. They use only the stdlib (no external deps)
because they execute inside the Modal sandbox image.
"""

from __future__ import annotations

import json
import re
from typing import Any


def peek(text: str, start: int = 0, length: int = 2000) -> str:
    """Return a slice of *text* starting at *start* for *length* chars.

    Useful for inspecting a portion of a long document without
    exceeding context limits.

    Args:
        text: The text to peek into.
        start: Starting position (default 0).
        length: Number of characters to return (default 2000).

    Returns:
        A substring of the input text.
    """
    return text[start : start + length]


def grep(text: str, pattern: str, *, context: int = 0) -> list[str]:
    """Return all lines in *text* that contain *pattern* (case-insensitive).

    Args:
        text: The text to search.
        pattern: Substring to match (case-insensitive).
        context: Number of surrounding lines to include (0 = matched line only).

    Returns:
        A list of matching lines (or line groups with context).
    """
    lines = text.splitlines()
    pat = re.compile(re.escape(pattern), re.IGNORECASE)
    hits: list[str] = []
    for idx, line in enumerate(lines):
        if pat.search(line):
            lo = max(0, idx - context)
            hi = min(len(lines), idx + context + 1)
            hits.append("\n".join(lines[lo:hi]))
    return hits


def chunk_by_size(text: str, size: int = 200_000, overlap: int = 0) -> list[str]:
    """Split *text* into fixed-size chunks with optional overlap.

    NOTE: This mirrors fleet_rlm.chunking (the canonical source).
    Keep defaults and logic in sync with chunking.py.

    Args:
        text: The text to chunk.
        size: Maximum chunk size in characters (default 200,000).
        overlap: Number of characters to overlap between chunks (default 0).

    Returns:
        List of text chunks.

    Raises:
        ValueError: If size <= 0, overlap < 0, or overlap >= size.
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


def chunk_by_headers(
    text: str,
    pattern: str = r"^#{1,3} ",
    flags: int = re.MULTILINE,
) -> list[dict[str, Any]]:
    """Split *text* at lines matching *pattern* (regex).

    Args:
        text: The text to chunk.
        pattern: Regex pattern for header lines (default: markdown headers #, ##, ###).
        flags: Regex flags to apply (default: MULTILINE).

    Returns:
        List of dicts with 'header', 'content', and 'start_pos' keys.
    """
    if not text:
        return []

    compiled = re.compile(pattern, flags | re.MULTILINE)
    matches = list(compiled.finditer(text))

    if not matches:
        return [{"header": "", "content": text.strip(), "start_pos": 0}]

    parts: list[dict[str, Any]] = []

    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            parts.append({"header": "", "content": preamble, "start_pos": 0})

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

        parts.append({"header": header, "content": content, "start_pos": match.start()})
    return parts


def chunk_by_timestamps(
    text: str,
    pattern: str = r"^\d{4}-\d{2}-\d{2}[T ]",
    flags: int = re.MULTILINE,
) -> list[dict[str, Any]]:
    """Split log-style text by timestamp boundaries.

    Args:
        text: The text to chunk.
        pattern: Regex pattern for timestamp lines (default: ISO-like dates).
        flags: Regex flags to apply (default: MULTILINE).

    Returns:
        List of dicts with 'timestamp', 'content', and 'start_pos' keys.
    """
    if not text:
        return []

    compiled = re.compile(pattern, flags)
    matches = list(compiled.finditer(text))

    if not matches:
        return [{"timestamp": "", "content": text, "start_pos": 0}]

    chunks: list[dict[str, Any]] = []

    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            chunks.append({"timestamp": "", "content": preamble, "start_pos": 0})

    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[match.start() : end].strip()
        timestamp = match.group(0).strip()
        chunks.append(
            {
                "timestamp": timestamp,
                "content": content,
                "start_pos": match.start(),
            }
        )

    return chunks


def chunk_by_json_keys(text: str) -> list[dict[str, Any]]:
    """Split a JSON object into per-key chunks.

    Args:
        text: JSON text to parse and chunk.

    Returns:
        List of dicts with 'key', 'content', and 'value_type' keys.

    Raises:
        ValueError: If text is not valid JSON or not a JSON object.
    """
    if not text or not text.strip():
        return []

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    chunks: list[dict[str, Any]] = []
    for key, value in data.items():
        chunks.append(
            {
                "key": key,
                "content": json.dumps(value, indent=2, default=str),
                "value_type": type(value).__name__,
            }
        )
    return chunks


# ------ Stateful buffers ------

_buffers: dict[str, list[Any]] = {}


def add_buffer(name: str, value: Any) -> None:
    """Append *value* to the named buffer.

    Args:
        name: Buffer name to append to.
        value: Value to append.
    """
    _buffers.setdefault(name, []).append(value)


def get_buffer(name: str) -> list[Any]:
    """Return the contents of the named buffer (empty list if missing).

    Args:
        name: Buffer name to retrieve.

    Returns:
        Copy of the buffer contents as a list.
    """
    return list(_buffers.get(name, []))


def clear_buffer(name: str | None = None) -> None:
    """Clear one or all buffers.

    Args:
        name: Buffer name to clear, or None to clear all buffers.
    """
    if name is None:
        _buffers.clear()
    else:
        _buffers.pop(name, None)


def reset_buffers() -> None:
    """Reset all buffers (for testing)."""
    _buffers.clear()
