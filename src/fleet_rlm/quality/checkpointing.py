"""Trusted checkpoint identity helpers for resumable GEPA runs."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .contracts import OptimizationRunSpec

_RESUMABLE_STATUSES = frozenset({"interrupted", "cancelled", "failed"})


class ResumeNotAllowedError(ValueError):
    """Raised when an optimization run cannot be resumed safely."""


def build_run_fingerprint(spec: OptimizationRunSpec) -> str:
    payload = spec.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def checkpoint_can_resume(*, expected_fingerprint: str, checkpoint_fingerprint: str | None) -> bool:
    return bool(checkpoint_fingerprint) and expected_fingerprint == checkpoint_fingerprint


def require_resume_fingerprint(
    *,
    stored_fingerprint: str | None,
    expected_fingerprint: str | None = None,
) -> str:
    """Return the trusted fingerprint or raise when resume is not exact-match.

    Explicit resume requires a non-empty stored fingerprint. When the caller
    supplies ``expected_fingerprint`` it must match byte-for-byte. Automatic
    recovery without this check is never allowed (Phase 8 protocol).
    """
    if not stored_fingerprint:
        raise ResumeNotAllowedError("Run has no trusted fingerprint and cannot be resumed.")
    if expected_fingerprint is not None and not checkpoint_can_resume(
        expected_fingerprint=expected_fingerprint,
        checkpoint_fingerprint=stored_fingerprint,
    ):
        raise ResumeNotAllowedError("Resume fingerprint does not match the original run.")
    return stored_fingerprint


def require_resumable_status(status: Any) -> str:
    """Normalize status and require a resumable terminal state."""
    value = status.value if hasattr(status, "value") else str(status)
    if value not in _RESUMABLE_STATUSES:
        raise ResumeNotAllowedError(
            f"Run status {value!r} is not resumable; only interrupted, cancelled, or failed runs may resume."
        )
    return value


__all__ = [
    "ResumeNotAllowedError",
    "build_run_fingerprint",
    "checkpoint_can_resume",
    "require_resumable_status",
    "require_resume_fingerprint",
]
