"""In-REPL context inspection and navigation helpers for Fleet RLM."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PageResult:
    """Result of paging through a long string or buffer."""

    content: str
    offset: int
    limit: int
    total_chars: int
    has_more: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "offset": self.offset,
            "limit": self.limit,
            "total_chars": self.total_chars,
            "has_more": self.has_more,
        }


def page(text: str, offset: int = 0, limit: int = 4000) -> PageResult:
    """Page through long context or tool outputs with bounded windowing.

    Parameters:
        text (str): Full text to page through.
        offset (int): Starting character index (0-indexed). Defaults to 0.
        limit (int): Maximum characters to return in this window. Defaults to 4000.

    Returns:
        PageResult: Window of text and paging metadata.
    """
    if not isinstance(text, str):
        text = str(text)
    total_chars = len(text)
    offset = max(0, min(offset, total_chars))
    limit = max(1, limit)
    end = min(offset + limit, total_chars)
    content = text[offset:end]
    has_more = end < total_chars
    return PageResult(
        content=content,
        offset=offset,
        limit=limit,
        total_chars=total_chars,
        has_more=has_more,
    )


@dataclass(frozen=True, slots=True)
class SearchMatch:
    """Individual match from searching in context."""

    line_number: int
    start_char: int
    end_char: int
    excerpt: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_number": self.line_number,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "excerpt": self.excerpt,
        }


def search(
    text: str,
    pattern: str,
    *,
    max_matches: int = 10,
    context_chars: int = 80,
    ignore_case: bool = True,
) -> list[SearchMatch]:
    """Search for pattern within text, returning matched line excerpts.

    Parameters:
        text (str): Text to search in.
        pattern (str): String or regex pattern to look for.
        max_matches (int): Maximum matches to return.
        context_chars (int): Surrounding characters to include in the excerpt.
        ignore_case (bool): Whether search is case-insensitive.
    """
    if not pattern:
        return []
    flags = re.IGNORECASE if ignore_case else 0
    try:
        regex = re.compile(re.escape(pattern), flags=flags)
    except re.error:
        regex = re.compile(re.escape(str(pattern)))

    matches: list[SearchMatch] = []
    lines = text.splitlines(keepends=True)
    line_starts: list[int] = [0]
    for line in lines:
        line_starts.append(line_starts[-1] + len(line))

    for m in regex.finditer(text):
        if len(matches) >= max_matches:
            break
        start, end = m.span()
        line_no = 1
        for idx, l_start in enumerate(line_starts[:-1]):
            if l_start <= start < line_starts[idx + 1]:
                line_no = idx + 1
                break

        ctx_start = max(0, start - context_chars)
        ctx_end = min(len(text), end + context_chars)
        excerpt = text[ctx_start:ctx_end].strip()

        matches.append(
            SearchMatch(
                line_number=line_no,
                start_char=start,
                end_char=end,
                excerpt=excerpt,
            )
        )
    return matches


class ContextInspector:
    """Namespaced context object for REPL models (e.g. ``context.page(...)``)."""

    def __init__(self, data: Mapping[str, str] | None = None) -> None:
        self._data: dict[str, str] = dict(data or {})

    def add(self, key: str, value: str) -> None:
        self._data[key] = str(value)

    def page(self, key_or_text: str, offset: int = 0, limit: int = 4000) -> PageResult:
        """Page through a registered key or direct text."""
        raw = self._data.get(key_or_text, key_or_text)
        return page(raw, offset=offset, limit=limit)

    def search(
        self,
        key_or_text: str,
        pattern: str,
        *,
        max_matches: int = 10,
        context_chars: int = 80,
    ) -> list[SearchMatch]:
        """Search within a registered key or direct text."""
        raw = self._data.get(key_or_text, key_or_text)
        return search(raw, pattern, max_matches=max_matches, context_chars=context_chars)


__all__ = [
    "ContextInspector",
    "PageResult",
    "SearchMatch",
    "page",
    "search",
]
