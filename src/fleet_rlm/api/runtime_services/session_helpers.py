"""Shared helpers for session route and websocket compatibility code."""

from __future__ import annotations

from typing import cast
import uuid

from fastapi import HTTPException


def string_or_default(value: object, default: str) -> str:
    """Return a non-empty string value or a fallback."""
    return value if isinstance(value, str) and value else default


def optional_string(value: object) -> str | None:
    """Return a non-empty string value or ``None``."""
    return value if isinstance(value, str) and value else None


def parse_session_uuid(session_id: str) -> uuid.UUID:
    """Parse a repository-backed session UUID or raise route-compatible 404."""
    try:
        return uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


def parse_legacy_session_id(session_id: str) -> int:
    """Parse a local-store legacy integer session id or raise 404."""
    try:
        return int(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


def session_external_id(metadata: object) -> str | None:
    """Return external session id from metadata JSON-like payloads."""
    if not isinstance(metadata, dict):
        return None
    metadata_dict = cast(dict[str, object], metadata)
    return optional_string(metadata_dict.get("external_session_id"))


def parse_legacy_session_key_owner(key: object) -> tuple[str | None, str | None]:
    """Parse legacy in-memory session-cache keys into workspace/user ids."""
    if not isinstance(key, str):
        return None, None
    if key.startswith("owner:"):
        return None, None
    workspace_id, separator, remainder = key.partition(":")
    if not separator:
        return None, None
    user_id, separator, _session_id = remainder.partition(":")
    if not separator:
        return None, None
    return (
        workspace_id or None,
        user_id or None,
    )


__all__ = [
    "optional_string",
    "parse_legacy_session_id",
    "parse_legacy_session_key_owner",
    "parse_session_uuid",
    "session_external_id",
    "string_or_default",
]
