"""Tests for LLM profile model catalog fetch behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from fleet_rlm.integrations.llm_profiles.model_catalog import (
    ModelCatalogEntry,
    ModelCatalogResult,
    _fetch_anthropic_compatible_models,
    _litellm_model_id,
    fetch_profile_model_catalog,
    invalidate_profile_catalog,
    normalize_google_openai_model_id,
)
from fleet_rlm.integrations.llm_profiles.types import LlmProviderProfileRecord


def _profile(
    *, provider_type: str = "openai_compatible", api_base: str = "https://example.com/v1"
) -> LlmProviderProfileRecord:
    return LlmProviderProfileRecord(
        id=uuid4(),
        name="Test profile",
        provider_type=provider_type,  # type: ignore[arg-type]
        api_base=api_base,
        api_key_ciphertext="encrypted",
        metadata_json={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_normalize_google_openai_model_id_strips_models_prefix() -> None:
    assert normalize_google_openai_model_id("models/gemini-3.5-flash") == "gemini-3.5-flash"
    assert normalize_google_openai_model_id("gemini-3.5-flash") == "gemini-3.5-flash"


def test_litellm_model_id_for_google_uses_openai_compat_ids() -> None:
    assert _litellm_model_id("google", "models/gemini-3.1-flash-lite") == "openai/gemini-3.1-flash-lite"


def test_litellm_model_id_openai_compatible_is_raw() -> None:
    """Non-LiteLLM OpenAI-compatible endpoints use the raw model id (no openai/ prefix)."""
    assert _litellm_model_id("openai_compatible", "glm-5.2") == "glm-5.2"
    assert _litellm_model_id("openai_compatible", "openai/glm-5.2") == "openai/glm-5.2"


def test_litellm_model_id_litellm_proxy_keeps_prefix() -> None:
    """LiteLLM-proxy endpoints keep the litellm openai/ prefix."""
    assert _litellm_model_id("litellm_proxy", "glm-5.2") == "openai/glm-5.2"


def test_build_lm_kwargs_openai_compatible_adds_custom_provider() -> None:
    from fleet_rlm.integrations.llm_profiles.resolver import build_lm_kwargs_from_resolved
    from fleet_rlm.integrations.llm_profiles.types import ResolvedRoleLmConfig

    config = ResolvedRoleLmConfig(
        role="planner",
        profile_id=uuid4(),
        profile_name="p",
        model_id="glm-5.2",
        litellm_model="glm-5.2",
        api_key="sk-x",
        api_base="https://maas.aliyuncs.com/v1",
        provider_type="openai_compatible",
    )
    kwargs = build_lm_kwargs_from_resolved(config)
    assert kwargs["model"] == "glm-5.2"
    assert kwargs["custom_llm_provider"] == "openai"


def test_build_lm_kwargs_litellm_proxy_has_no_custom_provider() -> None:
    from fleet_rlm.integrations.llm_profiles.resolver import build_lm_kwargs_from_resolved
    from fleet_rlm.integrations.llm_profiles.types import ResolvedRoleLmConfig

    config = ResolvedRoleLmConfig(
        role="planner",
        profile_id=uuid4(),
        profile_name="p",
        model_id="glm-5.2",
        litellm_model="openai/glm-5.2",
        api_key="sk-x",
        api_base="https://my-litellm-proxy/v1",
        provider_type="litellm_proxy",
    )
    kwargs = build_lm_kwargs_from_resolved(config)
    assert kwargs["model"] == "openai/glm-5.2"
    assert "custom_llm_provider" not in kwargs


def test_litellm_model_id_anthropic_compatible_is_raw() -> None:
    """Anthropic-compatible endpoints (POST /v1/messages) use the raw model id."""
    assert _litellm_model_id("anthropic_compatible", "claude-sonnet-4") == "claude-sonnet-4"


def test_build_lm_kwargs_anthropic_compatible_adds_custom_provider() -> None:
    from fleet_rlm.integrations.llm_profiles.resolver import build_lm_kwargs_from_resolved
    from fleet_rlm.integrations.llm_profiles.types import ResolvedRoleLmConfig

    config = ResolvedRoleLmConfig(
        role="planner",
        profile_id=uuid4(),
        profile_name="p",
        model_id="claude-sonnet-4",
        litellm_model="claude-sonnet-4",
        api_key="sk-x",
        api_base="https://my-gateway/anthropic",
        provider_type="anthropic_compatible",
    )
    kwargs = build_lm_kwargs_from_resolved(config)
    assert kwargs["model"] == "claude-sonnet-4"
    assert kwargs["custom_llm_provider"] == "anthropic"


@pytest.mark.asyncio
async def test_fetch_anthropic_compatible_models_uses_x_api_key(monkeypatch) -> None:
    """GET {api_base}/v1/models with x-api-key + anthropic-version headers."""
    captured: dict[str, object] = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "claude-sonnet-4"}, {"id": "claude-haiku-4"}]}

    class _FakeClient:
        def __init__(self, *_, **__):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, url, headers):
            captured["url"] = url
            captured["headers"] = headers
            return _FakeResponse()

    monkeypatch.setattr(_fetch_anthropic_compatible_models.__globals__["httpx"], "AsyncClient", _FakeClient)

    entries = await _fetch_anthropic_compatible_models(api_base="https://my-gateway/anthropic", api_key="sk-x")

    assert captured["url"] == "https://my-gateway/anthropic/v1/models"
    assert captured["headers"] == {"x-api-key": "sk-x", "anthropic-version": "2023-06-01"}
    assert [e.id for e in entries] == ["claude-haiku-4", "claude-sonnet-4"]


@pytest.mark.asyncio
async def test_validate_profile_via_models_catalog_success_and_error(monkeypatch) -> None:
    from fleet_rlm.integrations.llm_profiles import model_catalog as mod

    profile = _profile(provider_type="openai_compatible", api_base="https://x/v1")
    anth_profile = _profile(provider_type="anthropic_compatible", api_base="https://x/anthropic")

    async def fake_fetch(prof, *, force_refresh=False):
        assert force_refresh is True
        if prof is anth_profile:
            return ModelCatalogResult(
                models=[ModelCatalogEntry(id="claude-sonnet-4", label="c", litellm_model="claude-sonnet-4")]
            )
        if prof is profile:
            return ModelCatalogResult(models=[ModelCatalogEntry(id="glm-5.2", label="g", litellm_model="glm-5.2")])
        return ModelCatalogResult(models=[], error="401 Unauthorized")

    monkeypatch.setattr(mod, "fetch_profile_model_catalog", fake_fetch)

    ok, preview, err = await mod.validate_profile_via_models_catalog(profile)
    assert ok is True and err is None
    assert "/models" in preview and "glm-5.2" in preview

    ok, preview, err = await mod.validate_profile_via_models_catalog(anth_profile)
    assert ok is True and err is None
    assert "/v1/models" in preview

    bad = _profile(provider_type="openai_compatible", api_base="https://x/v1")
    ok, preview, err = await mod.validate_profile_via_models_catalog(bad)
    assert ok is False and preview is None
    assert err == "401 Unauthorized"


@pytest.mark.asyncio
async def test_fetch_profile_model_catalog_returns_error_instead_of_raising(monkeypatch) -> None:
    profile = _profile()

    async def _raise(*_args, **_kwargs):
        raise ValueError("connection refused")

    monkeypatch.setattr(
        "fleet_rlm.integrations.llm_profiles.model_catalog._fetch_provider_models",
        _raise,
    )
    invalidate_profile_catalog(str(profile.id))

    result = await fetch_profile_model_catalog(profile, force_refresh=True)

    assert isinstance(result, ModelCatalogResult)
    assert result.models == []
    assert result.error == "connection refused"


@pytest.mark.asyncio
async def test_fetch_profile_model_catalog_uses_anthropic_fallback_on_error(monkeypatch) -> None:
    profile = _profile(provider_type="anthropic", api_base="")

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(
        "fleet_rlm.integrations.llm_profiles.model_catalog._fetch_provider_models",
        _raise,
    )
    invalidate_profile_catalog(str(profile.id))

    result = await fetch_profile_model_catalog(profile, force_refresh=True)

    assert result.models
    assert result.error == "upstream unavailable"
