"""Unit tests for normalized dspy.LM construction helpers."""

from __future__ import annotations

from fleet_rlm_clean.rlm.lm_factory import normalize_model_id, sanitize_base_url


def test_sanitize_base_url_accepts_https_and_strips_comments() -> None:
    assert sanitize_base_url("https://opencode.ai/zen/v1") == "https://opencode.ai/zen/v1"
    assert sanitize_base_url("https://opencode.ai/zen/v1/") == "https://opencode.ai/zen/v1"
    assert (
        sanitize_base_url("https://opencode.ai/zen/v1'   # real gateway")
        == "https://opencode.ai/zen/v1"
    )
    assert sanitize_base_url("'https://example.com/v1'") == "https://example.com/v1"


def test_sanitize_base_url_rejects_keys_and_empty() -> None:
    assert sanitize_base_url(None) is None
    assert sanitize_base_url("") is None
    assert sanitize_base_url("sk-ws-H.not-a-url") is None
    assert sanitize_base_url("openai.com/v1") is None  # missing scheme


def test_normalize_model_id_adds_openai_prefix_for_compatible_bases() -> None:
    assert (
        normalize_model_id("deepseek-v4-flash-free", base_url="https://opencode.ai/zen/v1")
        == "openai/deepseek-v4-flash-free"
    )
    assert (
        normalize_model_id("openai/gpt-4o-mini", base_url="https://opencode.ai/zen/v1")
        == "openai/gpt-4o-mini"
    )
    assert normalize_model_id("anthropic/claude-sonnet-4", base_url=None) == "anthropic/claude-sonnet-4"
