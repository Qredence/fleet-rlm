"""Log-safe formatting helpers.

Keeps control-character sanitisation in one place so that untrusted values
cannot inject newlines into structured log output.
"""

from __future__ import annotations


def sanitize_for_log(value: object) -> str:
    """Strip control characters for safe log interpolation."""
    return str(value).replace("\r", "\\r").replace("\n", "\\n")


__all__ = ["sanitize_for_log"]
