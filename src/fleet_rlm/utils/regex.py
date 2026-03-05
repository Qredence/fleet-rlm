"""Regex-only utility helpers.

This module intentionally keeps text extraction helpers separate from
agent tool registries (`fleet_rlm.react.tools`) to reduce naming ambiguity.
"""

from __future__ import annotations

import re


def regex_extract(text: str, pattern: str, flags: int = 0) -> list:
    """Extract all regex matches from text using a compiled pattern.

    Args:
        text: The source text to search within.
        pattern: The regular expression pattern to match.
        flags: Optional regex flags (e.g., re.IGNORECASE, re.MULTILINE).

    Returns:
        A list of all matches found.
    """

    compiled = re.compile(pattern, flags)
    return compiled.findall(text)


__all__ = ["regex_extract"]
