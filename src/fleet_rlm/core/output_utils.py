"""Output utilities for ModalInterpreter.

This module provides pure utility functions for processing sandbox output,
extracted from the main interpreter for better maintainability.

Functions:
    - _redact_sensitive_text: Redacts API keys and tokens from text
    - _summarize_stdout: Summarizes long output to prevent context pollution
"""

from __future__ import annotations

import re


def _redact_sensitive_text(text: str) -> str:
    """Redact potentially sensitive information from text.

    Scans for and masks:
        - API keys (sk-... format)
        - Authorization headers with Bearer tokens
        - Key/value pairs containing api_key, token, or secret

    Args:
        text: The text to redact.

    Returns:
        The text with sensitive values replaced by ***REDACTED***.

    Example:
        >>> _redact_sensitive_text("api_key=sk-abc123xyz")
        'api_key=sk-***REDACTED***'
    """
    redacted = text
    # Redact likely API keys/tokens.
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
    """Summarize stdout output to prevent context window pollution.

    As described in the RLM paper (Section 2), long stdout outputs can
    pollute the LLM's context window during recursive iterations. This
    function returns metadata about the output instead of the full content
    when the output exceeds the configured threshold.

    The summary includes:
        - Total character count
        - Line count
        - A short prefix of the output (first N chars)
        - Indication if output was truncated

    Args:
        output: The stdout output from sandbox execution.
        threshold: Character threshold above which output is summarized.
            Defaults to 10000.
        prefix_len: Number of characters to include in summary prefix.
            Defaults to 200.

    Returns:
        Either the original output (if short) or a metadata summary
        (if long and summarization is enabled).

    Example:
        Short output (under threshold):
            "Hello, world!"

        Long output (over threshold):
            "[Output: 1,247 chars, 42 lines]\\n"
            'Prefix: "First 200 chars of output..."'
    """
    if len(output) <= threshold:
        return output

    # Calculate metadata
    total_chars = len(output)
    line_count = output.count("\n")
    actual_prefix_len = min(prefix_len, len(output))
    prefix = output[:actual_prefix_len]

    # Escape newlines in prefix for cleaner display
    prefix_display = prefix.replace("\n", "\\n")

    # Truncate prefix if it was cut mid-line
    if len(prefix) == actual_prefix_len and actual_prefix_len < len(output):
        prefix_display += "..."

    summary = (
        f"[Output: {total_chars:,} chars, {line_count} lines]\n"
        f'Prefix: "{prefix_display}"'
    )

    return summary
