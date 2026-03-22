"""Unified environment-variable parsing helpers.

Consolidates ``_env_bool``, ``_env_int``, and ``_env_csv`` that were
previously duplicated in ``core.config``, ``server.config``, and
``analytics.config``.
"""

from __future__ import annotations


def env_bool(value: str | None, *, default: bool) -> bool:
    """Parse a boolean from common environment-variable string forms.

    Recognises ``1/true/yes/on`` as *True* and ``0/false/no/off`` as
    *False* (case-insensitive).  Returns *default* for ``None`` or
    unrecognised values.
    """
    if value is None:
        return default
    candidate = value.strip().lower()
    if candidate in {"1", "true", "yes", "on"}:
        return True
    if candidate in {"0", "false", "no", "off"}:
        return False
    return default


def env_int(value: str | None, *, default: int) -> int:
    """Parse a positive integer from an environment-variable string.

    Returns *default* when the value is ``None``, non-numeric, or ≤ 0.
    """
    if value is None:
        return default
    try:
        parsed = int(value.strip())
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def env_csv(value: str | None, *, default: list[str]) -> list[str]:
    """Split a comma-separated environment variable into a list of strings.

    Empty items are dropped.  Returns *default* when the value is
    ``None`` or yields no items after stripping.
    """
    if value is None:
        return default
    items = [item.strip() for item in value.split(",")]
    cleaned = [item for item in items if item]
    return cleaned or default
