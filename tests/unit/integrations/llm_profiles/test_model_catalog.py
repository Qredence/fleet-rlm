"""Tests for LLM profile model catalog fetch behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from fleet_rlm.integrations.llm_profiles.model_catalog import (
    ModelCatalogResult,
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
