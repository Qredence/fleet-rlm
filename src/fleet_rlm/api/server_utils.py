"""Shared server router utilities."""

from __future__ import annotations

import re

from fleet_rlm.integrations.database.models import SandboxProvider


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
    return cleaned[:128] or default_value


def parse_model_identity(raw_model: object) -> tuple[str | None, str | None]:
    """Extract (provider, model_name) from a raw model string.

    Returns (None, None) for non-string input.
    Returns (None, raw_model) for unqualified names.
    Returns (provider, name) for ``provider/name`` format.
    """
    if not isinstance(raw_model, str):
        return None, None
    if "/" in raw_model:
        provider, name = raw_model.split("/", 1)
        return provider, name
    return None, raw_model


def resolve_sandbox_provider(raw: str) -> SandboxProvider:
    """Map a config string to the SandboxProvider enum."""
    normalized = raw.strip().lower()
    try:
        return SandboxProvider(normalized)
    except ValueError:
        return SandboxProvider.MODAL
