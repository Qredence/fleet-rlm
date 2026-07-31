"""Unit tests for normalized dspy.LM construction helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from fleet_rlm.config import Settings
from fleet_rlm.rlm.lm_factory import (
    build_model_bundle,
    has_llm_credentials,
    normalize_model_id,
    resolve_role_api_key,
    sanitize_base_url,
)


def test_model_bundle_applies_independent_role_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.rlm.lm_factory as factory

    monkeypatch.setenv("ROOT_KEY", "root-secret")
    monkeypatch.setenv("SUB_KEY", "sub-secret")
    build = MagicMock(side_effect=("root-lm", "sub-lm"))
    monkeypatch.setattr(factory, "build_lm", build)
    settings = Settings(
        _env_file=None,
        root_model="openai/root",
        sub_model="openai/sub",
        root_llm_api_key_env="ROOT_KEY",
        sub_llm_api_key_env="SUB_KEY",
        root_llm_max_tokens=101,
        sub_llm_max_tokens=202,
        root_llm_cache=True,
        sub_llm_cache=False,
        root_llm_num_retries=1,
        sub_llm_num_retries=4,
        sub_llm_temperature=0.3,
        root_llm_reasoning_effort="none",
    )

    bundle = factory.build_model_bundle(settings)

    assert bundle.root_lm == "root-lm"
    assert bundle.sub_lm == "sub-lm"
    assert build.call_args_list[0].kwargs["max_tokens"] == 101
    assert build.call_args_list[0].kwargs["cache"] is True
    assert build.call_args_list[0].kwargs["reasoning_effort"] == "none"
    assert build.call_args_list[1].kwargs["max_tokens"] == 202
    assert build.call_args_list[1].kwargs["cache"] is False
    assert build.call_args_list[1].kwargs["temperature"] == 0.3


def test_build_lm_allows_reasoning_effort_only_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.rlm.lm_factory as factory

    lm = MagicMock(side_effect=("default-lm", "bounded-lm"))
    monkeypatch.setattr(factory.dspy, "LM", lm)

    default = factory.build_lm("openai/default", api_key=None)
    bounded = factory.build_lm("openai/bounded", api_key=None, reasoning_effort="none")

    assert default == "default-lm"
    assert bounded == "bounded-lm"
    assert "reasoning_effort" not in lm.call_args_list[0].kwargs
    assert "allowed_openai_params" not in lm.call_args_list[0].kwargs
    assert lm.call_args_list[1].kwargs["reasoning_effort"] == "none"
    assert lm.call_args_list[1].kwargs["allowed_openai_params"] == ["reasoning_effort"]


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
        "FLEET_OPENAI_API_KEY",
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
    with pytest.raises(RuntimeError, match="FLEET_OPENAI_API_KEY"):
        build_model_bundle(settings)


def test_whitespace_legacy_key_is_not_a_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_OPENAI_API_KEY", raising=False)
    settings = Settings(_env_file=None, llm_api_key=SecretStr("   "))

    assert has_llm_credentials(settings) is False


def test_legacy_generic_key_does_not_cross_provider_role_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    settings = Settings(
        _env_file=None,
        llm_api_key=SecretStr("legacy-key"),
        root_llm_api_key_env="DATABRICKS_TOKEN",
        sub_llm_api_key_env="DATABRICKS_TOKEN",
    )

    assert resolve_role_api_key(settings, settings.llm_role("root")) is None
