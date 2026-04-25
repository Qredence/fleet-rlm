"""Regex-only utility helpers.

This module intentionally keeps text extraction helpers separate from
agent tool registries (`fleet_rlm.runtime.tools`) to reduce naming ambiguity.
"""

from __future__ import annotations

import re


def regex_extract(text: str, pattern: str, flags: int = 0) -> list:
    """Return all non-overlapping matches of pattern in text."""
    return re.findall(pattern, text, flags)


__all__ = ["regex_extract"]
