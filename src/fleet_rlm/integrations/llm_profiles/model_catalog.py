"""Dynamic model catalog fetchers for LLM provider profiles.

Two fetch patterns cover all three wire formats:
  - Standard ``GET /models`` with ``Authorization: Bearer <key>`` for both
    ``openai_responses`` and ``openai_chat_completion`` (OpenAI, OpenRouter,
    vLLM, Ollama, Alibaba MaaS, Gemini's `/v1beta/openai/` endpoint, etc.).
  - Anthropic ``GET /v1/models`` with ``x-api-key`` + ``anthropic-version``
    headers for ``anthropic_messages`` (Anthropic-compatible proxies).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from .store import decrypt_profile_api_key
from .types import LlmProviderProfileRecord, LlmProviderType

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
    resolved_model_id: str


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


def _resolve_model_id(provider_type: LlmProviderType, model_id: str) -> str:
    """Normalize a model id for the given wire format.

    All three formats accept either a LiteLLM-recognized provider prefix
    (``openai/gpt-4o``, ``anthropic/claude-...``) passed through verbatim, or
    a bare model id (preserved as-is so ``custom_llm_provider`` from
    ``build_lm_kwargs_from_resolved`` does the routing against ``api_base``).
    """
    normalized = model_id.strip()
    if "/" in normalized:
        return normalized
    return normalized


def _entry(model_id: str, provider_type: LlmProviderType) -> ModelCatalogEntry:
    resolved_model_id = _resolve_model_id(provider_type, model_id)
    return ModelCatalogEntry(id=model_id, label=model_id, resolved_model_id=resolved_model_id)


async def _fetch_openai_format_models(*, api_base: str, api_key: str) -> list[ModelCatalogEntry]:
    """Standard ``GET /models`` with ``Authorization: Bearer <key>``."""
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
    return [_entry(model_id, "openai_chat_completion") for model_id in ids if model_id]


async def _fetch_anthropic_format_models(*, api_base: str, api_key: str) -> list[ModelCatalogEntry]:
    """Fetch models from a custom Anthropic-format endpoint (POST /v1/messages).

    Anthropic exposes ``GET {api_base}/v1/models`` and authenticates with the
    ``x-api-key`` header plus an ``anthropic-version`` header. The response
    shape (``{"data": [{"id": ...}]}``) matches the OpenAI /models payload.
    """
    base = _normalize_base_url(api_base)
    url = urljoin(f"{base}/", "v1/models")
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
    models = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(models, list):
        return []
    ids = sorted({str(item.get("id", "")).strip() for item in models if isinstance(item, dict) and item.get("id")})
    return [_entry(model_id, "anthropic_messages") for model_id in ids if model_id]


async def _fetch_provider_models(profile: LlmProviderProfileRecord) -> list[ModelCatalogEntry]:
    api_key = decrypt_profile_api_key(profile)
    if not api_key:
        return []

    provider_type = profile.provider_type
    api_base = profile.api_base

    # Built-in Anthropic does not expose a public /models endpoint; return the
    # curated static catalog so users can still pick a model in the UI.
    if provider_type == "anthropic_messages" and not api_base:
        return [_entry(model_id, provider_type) for model_id in ANTHROPIC_STATIC_MODELS]

    if not api_base:
        return []

    if provider_type == "anthropic_messages":
        return await _fetch_anthropic_format_models(api_base=api_base, api_key=api_key)

    # openai_responses and openai_chat_completion both use the standard
    # Bearer /models pattern.
    return await _fetch_openai_format_models(api_base=api_base, api_key=api_key)


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
    if provider_type == "anthropic_messages":
        return ANTHROPIC_STATIC_MODELS
    return ()


def invalidate_profile_catalog(profile_id: str) -> None:
    _catalog_cache.pop(profile_id, None)


def catalog_to_payload(models: list[ModelCatalogEntry]) -> list[dict[str, Any]]:
    return [{"id": item.id, "label": item.label, "resolved_model_id": item.resolved_model_id} for item in models]


# Provider types whose endpoints expose an OpenAI- or Anthropic-style /models
# endpoint. For these, the connectivity Test validates via a lightweight
# GET /models (Bearer) or GET /v1/models (x-api-key) instead of a chat
# completion — no provider cold start, no token spend.
MODELS_ENDPOINT_PROVIDER_TYPES = frozenset({"openai_responses", "openai_chat_completion", "anthropic_messages"})


async def validate_profile_via_models_catalog(
    profile: LlmProviderProfileRecord,
) -> tuple[bool, str | None, str | None]:
    """Validate a profile via ``GET /models`` (or ``/v1/models`` for Anthropic).

    Bypasses the catalog cache (``force_refresh``) so the Test actually hits the
    endpoint. Returns ``(ok, output_preview, error)`` — caller redacts ``error``
    if it may contain the profile api_key.
    """
    catalog = await fetch_profile_model_catalog(profile, force_refresh=True)
    if catalog.models:
        ids = [entry.id for entry in catalog.models[:5]]
        suffix = "v1/models" if profile.provider_type == "anthropic_messages" else "models"
        preview = f"GET {profile.api_base.rstrip('/')}/{suffix} OK — {len(catalog.models)} models: {', '.join(ids)}"
        return True, preview, None
    return False, None, catalog.error or "No models returned by /models endpoint."
