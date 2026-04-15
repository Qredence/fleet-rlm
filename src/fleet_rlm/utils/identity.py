"""Identity and session-key helpers.

Centralised here so that both ``agent_host/`` and ``api/`` layers can import
without creating a cross-layer dependency.
"""

from __future__ import annotations

import hashlib
import re


def sanitize_id(value: str, default_value: str) -> str:
    """Restrict IDs to a safe path/key character subset (alphanumeric, ``_``, ``.``, ``-``).

    Consecutive dots (e.g. ``..``) are collapsed to ``-`` to prevent directory
    traversal when the result is embedded in a file path.
    """
    candidate = (value or "").strip()
    if not candidate:
        return default_value
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]", "-", candidate)
    cleaned = re.sub(r"\.{2,}", "-", cleaned)
    cleaned = cleaned.strip(".")
    if not re.search(r"[A-Za-z0-9]", cleaned):
        return default_value
    return cleaned[:128] or default_value


def owner_fingerprint(tenant_claim: str, user_claim: str) -> str:
    """Return a collision-resistant owner fingerprint for session scoping."""
    payload = f"{tenant_claim}\0{user_claim}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def session_key(
    tenant_claim: str,
    user_claim: str,
    session_id: str | None = None,
) -> str:
    """Build a stable in-memory key for a stateful user/workspace session."""
    resolved_session_id = (session_id or "").strip() or "__default__"
    owner_id = owner_fingerprint(tenant_claim, user_claim)
    return f"owner:{owner_id}:{resolved_session_id}"


__all__ = ["owner_fingerprint", "sanitize_id", "session_key"]
