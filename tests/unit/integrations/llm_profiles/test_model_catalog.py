"""Tests for LLM profile model catalog fetch behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from fleet_rlm.integrations.llm_profiles.model_catalog import (
    ModelCatalogEntry,
    ModelCatalogResult,
    _fetch_anthropic_format_models,
    _resolve_model_id,
    fetch_profile_model_catalog,
    invalidate_profile_catalog,
)
from fleet_rlm.integrations.llm_profiles.types import LlmProviderProfileRecord


def _profile(
    *, provider_type: str = "openai_chat_completion", api_base: str = "https://example.com/v1"
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


def test_resolve_model_id_openai_chat_completion_passes_through() -> None:
    """OpenAI-compatible endpoints: bare ids stay bare; prefixed ids stay prefixed."""
    assert _resolve_model_id("openai_chat_completion", "glm-5.2") == "glm-5.2"
    assert _resolve_model_id("openai_chat_completion", "openai/glm-5.2") == "openai/glm-5.2"


def test_resolve_model_id_openai_responses_passes_through() -> None:
    assert _resolve_model_id("openai_responses", "gpt-4o") == "gpt-4o"
    assert _resolve_model_id("openai_responses", "openai/gpt-4o") == "openai/gpt-4o"


def test_resolve_model_id_anthropic_messages_passes_through() -> None:
    """Anthropic-compatible endpoints (POST /v1/messages) use the raw model id."""
    assert _resolve_model_id("anthropic_messages", "claude-sonnet-4") == "claude-sonnet-4"
    assert _resolve_model_id("anthropic_messages", "anthropic/claude-sonnet-4") == "anthropic/claude-sonnet-4"


def test_build_lm_kwargs_openai_chat_completion_adds_custom_provider() -> None:
    from fleet_rlm.integrations.llm_profiles.resolver import build_lm_kwargs_from_resolved
    from fleet_rlm.integrations.llm_profiles.types import ResolvedRoleLmConfig

    config = ResolvedRoleLmConfig(
        role="planner",
        profile_id=uuid4(),
        profile_name="p",
        model_id="glm-5.2",
        resolved_model_id="glm-5.2",
        api_key="sk-x",
        api_base="https://maas.aliyuncs.com/v1",
        provider_type="openai_chat_completion",
    )
    kwargs = build_lm_kwargs_from_resolved(config)
    assert kwargs["model"] == "glm-5.2"
    assert kwargs["custom_llm_provider"] == "openai"


def test_build_lm_kwargs_anthropic_messages_adds_custom_provider() -> None:
    from fleet_rlm.integrations.llm_profiles.resolver import build_lm_kwargs_from_resolved
    from fleet_rlm.integrations.llm_profiles.types import ResolvedRoleLmConfig

    config = ResolvedRoleLmConfig(
        role="planner",
        profile_id=uuid4(),
        profile_name="p",
        model_id="claude-sonnet-4",
        resolved_model_id="claude-sonnet-4",
        api_key="sk-x",
        api_base="https://my-gateway/anthropic",
        provider_type="anthropic_messages",
    )
    kwargs = build_lm_kwargs_from_resolved(config)
    assert kwargs["model"] == "claude-sonnet-4"
    assert kwargs["custom_llm_provider"] == "anthropic"


@pytest.mark.asyncio
async def test_fetch_anthropic_format_models_uses_x_api_key(monkeypatch) -> None:
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

    monkeypatch.setattr(_fetch_anthropic_format_models.__globals__["httpx"], "AsyncClient", _FakeClient)

    entries = await _fetch_anthropic_format_models(api_base="https://my-gateway/anthropic", api_key="sk-x")

    assert captured["url"] == "https://my-gateway/anthropic/v1/models"
    assert captured["headers"] == {"x-api-key": "sk-x", "anthropic-version": "2023-06-01"}
    assert [e.id for e in entries] == ["claude-haiku-4", "claude-sonnet-4"]


@pytest.mark.asyncio
async def test_validate_profile_via_models_catalog_success_and_error(monkeypatch) -> None:
    from fleet_rlm.integrations.llm_profiles import model_catalog as mod

    profile = _profile(provider_type="openai_chat_completion", api_base="https://x/v1")
    anth_profile = _profile(provider_type="anthropic_messages", api_base="https://x/anthropic")

    async def fake_fetch(prof, *, force_refresh=False):
        assert force_refresh is True
        if prof is anth_profile:
            return ModelCatalogResult(
                models=[ModelCatalogEntry(id="claude-sonnet-4", label="c", resolved_model_id="claude-sonnet-4")]
            )
        if prof is profile:
            return ModelCatalogResult(models=[ModelCatalogEntry(id="glm-5.2", label="g", resolved_model_id="glm-5.2")])
        return ModelCatalogResult(models=[], error="401 Unauthorized")

    monkeypatch.setattr(mod, "fetch_profile_model_catalog", fake_fetch)

    ok, preview, err = await mod.validate_profile_via_models_catalog(profile)
    assert ok is True and err is None
    assert "/models" in preview and "glm-5.2" in preview

    ok, preview, err = await mod.validate_profile_via_models_catalog(anth_profile)
    assert ok is True and err is None
    assert "/v1/models" in preview

    bad = _profile(provider_type="openai_chat_completion", api_base="https://x/v1")
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
    profile = _profile(provider_type="anthropic_messages", api_base="")

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
