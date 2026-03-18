"""Unit tests for analytics sanitization helpers."""

from __future__ import annotations

import pytest

from fleet_rlm.features.analytics.sanitization import (
    redact_sensitive,
    sanitize_text,
    truncate_text,
)
from tests.unit.fixtures_env import SANITIZATION_CASES


@pytest.mark.parametrize(("raw", "expected_snippets"), SANITIZATION_CASES[:1])
def test_redact_sensitive_masks_common_tokens(
    raw: str, expected_snippets: list[str]
) -> None:
    redacted = redact_sensitive(raw)

    for snippet in expected_snippets:
        assert snippet in redacted


@pytest.mark.parametrize(("text", "expected_snippets"), SANITIZATION_CASES[1:])
def test_redact_sensitive_preserves_non_sensitive_text(
    text: str, expected_snippets: list[str]
) -> None:
    redacted = redact_sensitive(text)
    for snippet in expected_snippets:
        assert snippet == redacted


def test_truncate_text_with_suffix() -> None:
    text = "abcdefghij"
    out = truncate_text(text, 5)
    assert out == "abcde...[truncated, 5 more chars]"


def test_sanitize_text_redacts_then_truncates() -> None:
    text = "secret=abcdef and this is a long tail"
    out = sanitize_text(text, redact=True, truncation_chars=12)

    assert out.startswith("secret=***RE")
    assert "truncated" in out
