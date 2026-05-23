"""Shared helpers for session route and websocket compatibility code."""

from __future__ import annotations

import uuid
from typing import cast

from fastapi import HTTPException


def string_or_default(value: object, default: str) -> str:
    """Return a non-empty string value or a fallback."""
    return value if isinstance(value, str) and value else default


def optional_string(value: object) -> str | None:
    """Return a non-empty string value or ``None``."""
    return value if isinstance(value, str) and value else None


def parse_session_uuid(session_id: str) -> uuid.UUID:
    """Parse the canonical repository-backed session UUID."""
    try:
        return uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


def session_external_id(metadata: object) -> str | None:
    """Return external session id from metadata JSON-like payloads."""
    if not isinstance(metadata, dict):
        return None
    metadata_dict = cast(dict[str, object], metadata)
    return optional_string(metadata_dict.get("external_session_id"))


__all__ = [
    "optional_string",
    "parse_session_uuid",
    "session_external_id",
    "string_or_default",
]
