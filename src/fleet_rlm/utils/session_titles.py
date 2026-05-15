"""Helpers for deriving and recognizing durable chat session titles."""

from __future__ import annotations

import uuid

_DEFAULT_SESSION_TITLE = "Chat session"
_MAX_SESSION_TITLE_LENGTH = 60


def derive_session_title(user_message: str, *, fallback: str = _DEFAULT_SESSION_TITLE) -> str:
    """Return a user-facing session title derived from the first chat prompt."""
    normalized = " ".join(user_message.strip().split())
    if not normalized:
        return fallback
    if len(normalized) <= _MAX_SESSION_TITLE_LENGTH:
        return normalized
    return f"{normalized[: _MAX_SESSION_TITLE_LENGTH - 3].rstrip()}..."


def is_placeholder_session_title(
    title: str | None,
    *,
    external_session_id: str | None = None,
) -> bool:
    """Return whether a stored title is still a runtime/internal placeholder."""
    raw = (title or "").strip()
    if not raw:
        return True
    if raw == _DEFAULT_SESSION_TITLE:
        return True
    if external_session_id and raw == external_session_id:
        return True
    try:
        uuid.UUID(raw)
        return True
    except ValueError:
        pass
    if raw.startswith("Session "):
        suffix = raw.removeprefix("Session ").strip()
        if suffix.isdigit():
            return True
        try:
            uuid.UUID(suffix)
            return True
        except ValueError:
            return False
    return False
