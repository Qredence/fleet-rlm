"""Utility functions and helpers.

Provides small utility functions that don't belong in runtime/integrations.
"""

from __future__ import annotations

from .identity import owner_fingerprint, sanitize_id, session_key
from .logging import sanitize_for_log
from .paths import dedupe_paths, is_local_path
from .time import now_iso, utc_now

__all__ = [
    "dedupe_paths",
    "is_local_path",
    "now_iso",
    "owner_fingerprint",
    "sanitize_for_log",
    "sanitize_id",
    "session_key",
    "utc_now",
]
