from __future__ import annotations

import re


def regex_extract(text: str, pattern: str, flags: int = 0) -> list:
    """Extract regex matches from text using a compiled pattern."""

    compiled = re.compile(pattern, flags)
    return compiled.findall(text)
