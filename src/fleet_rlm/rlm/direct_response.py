"""Deterministic responses for requests that do not require RLM execution."""

from __future__ import annotations

import re

_PLAIN_GREETING = re.compile(r"(?:hi|hello|hey)(?: there)?[.!?,]*")


def direct_greeting_response(request: str) -> str | None:
    """Return the bounded default response for a standalone greeting."""
    normalized = " ".join(request.casefold().split())
    if _PLAIN_GREETING.fullmatch(normalized):
        return "Hi! How can I help you today?"
    return None
