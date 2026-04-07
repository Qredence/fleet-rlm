"""Output utilities for interpreter execution flows."""

from __future__ import annotations

import re


def _redact_sensitive_text(text: str) -> str:
    """Redact likely credentials from stdout/stderr text."""
    redacted = text
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-***REDACTED***", redacted)
    redacted = re.sub(
        r"(Authorization\s*:\s*Bearer\s+)[^\s]+",
        r"\1***REDACTED***",
        redacted,
        flags=re.IGNORECASE,
    )
    redacted = re.sub(
        r"((?:api[_-]?key|token|secret)\s*[=:]\s*)[^\s'\"\\]+",
        r"\1***REDACTED***",
        redacted,
        flags=re.IGNORECASE,
    )
    return redacted


def _summarize_stdout(
    output: str,
    *,
    threshold: int = 10000,
    prefix_len: int = 200,
) -> str:
    """Summarize long stdout output to avoid context pollution."""
    if len(output) <= threshold:
        return output

    total_chars = len(output)
    line_count = output.count("\n")
    actual_prefix_len = min(prefix_len, len(output))
    prefix_display = output[:actual_prefix_len].replace("\n", "\\n")
    if actual_prefix_len < len(output):
        prefix_display += "..."

    return (
        f"[Output: {total_chars:,} chars, {line_count} lines]\n"
        f'Prefix: "{prefix_display}"'
    )
