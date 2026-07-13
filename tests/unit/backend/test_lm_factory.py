"""Unit tests for normalized dspy.LM construction helpers."""

from __future__ import annotations

import pytest

from fleet_rlm.config import Settings
from fleet_rlm.rlm.lm_factory import build_model_bundle, normalize_model_id, sanitize_base_url


def test_sanitize_base_url_accepts_https_and_strips_comments() -> None:
    assert sanitize_base_url("https://opencode.ai/zen/v1") == "https://opencode.ai/zen/v1"
    assert sanitize_base_url("https://opencode.ai/zen/v1/") == "https://opencode.ai/zen/v1"
    assert sanitize_base_url("https://opencode.ai/zen/v1'   # real gateway") == "https://opencode.ai/zen/v1"
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
    assert normalize_model_id("openai/gpt-4o-mini", base_url="https://opencode.ai/zen/v1") == "openai/gpt-4o-mini"
    assert normalize_model_id("anthropic/claude-sonnet-4", base_url=None) == "anthropic/claude-sonnet-4"


def test_runtime_does_not_accept_provider_environment_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "FLEET_LLM_API_KEY",
        "FLEET_LLM_BASE_URL",
        "FLEET_ROOT_MODEL",
        "FLEET_SUB_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "provider-alias-must-not-be-used")
    monkeypatch.setenv("DSPY_LM_MODEL", "provider/alias-model")

    settings = Settings(_env_file=None)

    assert settings.llm_api_key is None
    assert settings.root_model == "openai/gpt-4o-mini"
    with pytest.raises(RuntimeError, match="FLEET_LLM_API_KEY"):
        build_model_bundle(settings)
