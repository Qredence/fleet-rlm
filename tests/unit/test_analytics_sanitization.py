"""Unit tests for analytics sanitization helpers."""

from __future__ import annotations

from fleet_rlm.analytics.sanitization import (
    redact_sensitive,
    sanitize_text,
    truncate_text,
)


def test_redact_sensitive_masks_common_tokens() -> None:
    raw = (
        "api_key=sk-abc12345DEF token=my-secret-token Authorization: Bearer abc.def.ghi"
    )
    redacted = redact_sensitive(raw)

    assert "api_key=***REDACTED***" in redacted
    assert "token=***REDACTED***" in redacted
    assert "Authorization: Bearer ***REDACTED***" in redacted


def test_redact_sensitive_preserves_non_sensitive_text() -> None:
    text = "hello world, no secrets here"
    assert redact_sensitive(text) == text


def test_truncate_text_with_suffix() -> None:
    text = "abcdefghij"
    out = truncate_text(text, 5)
    assert out == "abcde...[truncated, 5 more chars]"


def test_sanitize_text_redacts_then_truncates() -> None:
    text = "secret=abcdef and this is a long tail"
    out = sanitize_text(text, redact=True, truncation_chars=12)

    assert out.startswith("secret=***RE")
    assert "truncated" in out
