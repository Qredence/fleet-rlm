"""Utility tools for text extraction and processing.

This module provides helper functions used by DSPy signatures during
the RLM execution process. These tools can be registered with the
interpreter and called from within the sandboxed code.

Available tools:
    - regex_extract: Extract pattern matches using regular expressions
"""

from __future__ import annotations

import re


def regex_extract(text: str, pattern: str, flags: int = 0) -> list:
    """Extract all regex matches from text using a compiled pattern.

    This function compiles the given regex pattern and returns all
    non-overlapping matches found in the text. It's designed to be
    called from within the Modal sandbox during RLM execution.

    Args:
        text: The source text to search within.
        pattern: The regular expression pattern to match.
        flags: Optional regex flags (e.g., re.IGNORECASE, re.MULTILINE).
            Defaults to 0 (no flags).

    Returns:
        A list of all matches found. If the pattern contains groups,
        returns a list of tuples containing group values. If no matches
        are found, returns an empty list.

    Example:
        >>> regex_extract("Hello world!", r"\\w+")
        ['Hello', 'world']
        >>> regex_extract("Code: python", r"Code: (\\w+)", re.IGNORECASE)
        ['python']
    """

    compiled = re.compile(pattern, flags)
    return compiled.findall(text)
