"""Dynamic model catalog fetchers for LLM provider profiles."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from .store import decrypt_profile_api_key
from .types import LITELLM_PROVIDER_PREFIX, LlmProviderProfileRecord, LlmProviderType

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300

ANTHROPIC_STATIC_MODELS: tuple[str, ...] = (
    "claude-sonnet-4-20250514",
    "claude-3-7-sonnet-latest",
    "claude-3-5-sonnet-latest",
    "claude-3-5-haiku-latest",
    "claude-3-opus-latest",
)


@dataclass(slots=True)
class ModelCatalogEntry:
    id: str
    label: str
    litellm_model: str


@dataclass(slots=True)
class ModelCatalogResult:
    models: list[ModelCatalogEntry]
    error: str | None = None


@dataclass(slots=True)
class _CacheEntry:
    expires_at: float
    result: ModelCatalogResult


_catalog_cache: dict[str, _CacheEntry] = {}


def _cache_key(profile: LlmProviderProfileRecord) -> str:
    return str(profile.id)


def _normalize_base_url(api_base: str) -> str:
    return api_base.rstrip("/")


def normalize_google_openai_model_id(model_id: str) -> str:
    """Strip the native ``models/`` prefix from Gemini REST model ids."""
    normalized = model_id.strip()
    if normalized.startswith("models/"):
        return normalized.removeprefix("models/")
    return normalized


def _litellm_model_id(provider_type: LlmProviderType, model_id: str) -> str:
    normalized = model_id.strip()
    if provider_type == "google":
        bare = normalize_google_openai_model_id(normalized)
        if "/" in bare:
            return bare
        return f"{LITELLM_PROVIDER_PREFIX['google']}/{bare}"
    if "/" in normalized:
        return normalized
    prefix = {
        "openai": "openai",
        "anthropic": "anthropic",
        "openai_compatible": "openai",
    }[provider_type]
    return f"{prefix}/{normalized}"


def _entry(model_id: str, provider_type: LlmProviderType) -> ModelCatalogEntry:
    litellm_model = _litellm_model_id(provider_type, model_id)
    return ModelCatalogEntry(id=model_id, label=model_id, litellm_model=litellm_model)


async def _fetch_openai_compatible_models(*, api_base: str, api_key: str) -> list[ModelCatalogEntry]:
    base = _normalize_base_url(api_base)
    url = urljoin(f"{base}/", "models")
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
    models = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(models, list):
        return []
    ids = sorted({str(item.get("id", "")).strip() for item in models if isinstance(item, dict) and item.get("id")})
    return [_entry(model_id, "openai_compatible") for model_id in ids if model_id]


async def _fetch_provider_models(profile: LlmProviderProfileRecord) -> list[ModelCatalogEntry]:
    api_key = decrypt_profile_api_key(profile)
    if not api_key:
        return []

    provider_type = profile.provider_type
    api_base = profile.api_base

    if provider_type == "anthropic":
        return [_entry(model_id, provider_type) for model_id in ANTHROPIC_STATIC_MODELS]

    if provider_type in {"openai", "google", "openai_compatible"}:
        if not api_base:
            return []
        entries = await _fetch_openai_compatible_models(api_base=api_base, api_key=api_key)
        if provider_type == "openai":
            return [
                ModelCatalogEntry(id=entry.id, label=entry.id, litellm_model=_litellm_model_id("openai", entry.id))
                for entry in entries
            ]
        if provider_type == "google":
            google_models: list[ModelCatalogEntry] = []
            seen: set[str] = set()
            for entry in entries:
                model_id = normalize_google_openai_model_id(entry.id)
                if not model_id or model_id in seen:
                    continue
                seen.add(model_id)
                google_models.append(
                    ModelCatalogEntry(id=model_id, label=model_id, litellm_model=model_id),
                )
            return google_models
        return entries

    return []


async def fetch_profile_model_catalog(
    profile: LlmProviderProfileRecord,
    *,
    force_refresh: bool = False,
) -> ModelCatalogResult:
    key = _cache_key(profile)
    now = time.monotonic()
    cached = _catalog_cache.get(key)
    if not force_refresh and cached is not None and cached.expires_at > now:
        return ModelCatalogResult(models=list(cached.result.models), error=cached.result.error)

    error: str | None = None
    try:
        models = await _fetch_provider_models(profile)
    except Exception as exc:
        logger.warning("Failed to fetch model catalog for profile %s: %s", profile.id, exc)
        if provider_fallback := _provider_fallback(profile.provider_type):
            models = [_entry(model_id, profile.provider_type) for model_id in provider_fallback]
            error = str(exc)
        else:
            models = []
            error = str(exc)

    result = ModelCatalogResult(models=models, error=error)
    _catalog_cache[key] = _CacheEntry(expires_at=now + CACHE_TTL_SECONDS, result=result)
    return ModelCatalogResult(models=list(models), error=error)


def _provider_fallback(provider_type: LlmProviderType) -> tuple[str, ...]:
    if provider_type == "anthropic":
        return ANTHROPIC_STATIC_MODELS
    return ()


def invalidate_profile_catalog(profile_id: str) -> None:
    _catalog_cache.pop(profile_id, None)


def catalog_to_payload(models: list[ModelCatalogEntry]) -> list[dict[str, Any]]:
    return [{"id": item.id, "label": item.label, "litellm_model": item.litellm_model} for item in models]
