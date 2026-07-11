"""Build RLMModelBundle via stock ``dspy.LM`` (normalized LiteLLM-backed API).

Follows https://dspy.ai/api/models/LM/ — model ids are ``provider/model`` strings;
credentials and OpenAI-compatible bases are passed as kwargs (``api_key``,
``api_base``). Does not call litellm directly.
"""

from __future__ import annotations

import os
import re
from typing import Any

import dspy

from fleet_rlm_clean.config import Settings
from fleet_rlm_clean.rlm.model_bundle import RLMModelBundle

# Values that look like secrets/keys must never be treated as base URLs.
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def sanitize_base_url(value: str | None) -> str | None:
    """Accept only http(s) bases; strip quotes, comments, and trailing junk."""
    if value is None:
        return None
    text = str(value).strip().strip("'\"")
    # Drop inline comments (common when pasting from docs into .env)
    if " #" in text:
        text = text.split(" #", 1)[0].rstrip().strip("'\"")
    if not text or not _URL_RE.match(text):
        return None
    return text.rstrip("/")


def normalize_model_id(model: str, *, base_url: str | None) -> str:
    """Ensure LiteLLM-style ``provider/model`` form used by ``dspy.LM``.

    Custom OpenAI-compatible gateways typically need the ``openai/`` provider
    prefix even when the model name is gateway-local.
    """
    cleaned = (model or "").strip().strip("'\"")
    if not cleaned:
        msg = "model id is required"
        raise ValueError(msg)
    if "/" in cleaned:
        return cleaned
    # Bare model name + custom base → OpenAI-compatible provider
    if base_url:
        return f"openai/{cleaned}"
    # Default inference: still require a provider for normalized API
    return f"openai/{cleaned}"


def _resolve_api_key(settings: Settings) -> str | None:
    if settings.llm_api_key is not None:
        value = settings.llm_api_key.get_secret_value()
        if value:
            return value
    # Prefer DSPy/fleet keys over OPENAI_API_KEY when that var holds a workspace token.
    for env_name in (
        "FLEET_CLEAN_LLM_API_KEY",
        "DSPY_LLM_API_KEY",
        "DSPY_LM_API_KEY",
        "OPENAI_API_KEY",
        "LLM_API_KEY",
    ):
        value = os.environ.get(env_name)
        if not value:
            continue
        # Skip values that are clearly not usable as bearer keys for OpenAI-compat.
        if value.startswith("http://") or value.startswith("https://"):
            continue
        return value
    return None


def _resolve_base_url(settings: Settings) -> str | None:
    candidates = [
        settings.llm_base_url,
        os.environ.get("FLEET_CLEAN_LLM_BASE_URL"),
        os.environ.get("DSPY_LM_API_BASE"),
        os.environ.get("DSPY_DELEGATE_LM_API_BASE"),
        os.environ.get("OPENAI_BASE_URL"),
        os.environ.get("OPENAI_API_BASE"),  # often mis-set; sanitize_base_url rejects non-URLs
        os.environ.get("OPENROUTER_API_BASE"),
    ]
    for value in candidates:
        url = sanitize_base_url(value)
        if url:
            return url
    return None


def _resolve_max_tokens(settings: Settings) -> int | None:
    raw = os.environ.get("FLEET_CLEAN_LLM_MAX_TOKENS") or os.environ.get("DSPY_LM_MAX_TOKENS")
    if raw:
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def _resolve_model_name(settings: Settings, *, role: str) -> str:
    if role == "root":
        return (
            settings.root_model
            or os.environ.get("FLEET_CLEAN_ROOT_MODEL")
            or os.environ.get("DSPY_LM_MODEL")
            or "openai/gpt-4o-mini"
        )
    return (
        settings.sub_model
        or os.environ.get("FLEET_CLEAN_SUB_MODEL")
        or os.environ.get("DSPY_LM_SMALL_MODEL")
        or os.environ.get("DSPY_DELEGATE_LM_MODEL")
        or settings.root_model
        or "openai/gpt-4o-mini"
    )


def build_lm(
    model: str,
    *,
    api_key: str | None,
    base_url: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    model_type: str = "chat",
    cache: bool = True,
    num_retries: int = 3,
) -> dspy.LM:
    """Construct a stock ``dspy.LM`` per the public LM constructor contract."""
    model_id = normalize_model_id(model, base_url=base_url)
    kwargs: dict[str, Any] = {
        "model_type": model_type,
        "cache": cache,
        "num_retries": num_retries,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["api_base"] = base_url
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if temperature is not None:
        kwargs["temperature"] = temperature
    return dspy.LM(model_id, **kwargs)


def build_model_bundle(settings: Settings) -> RLMModelBundle:
    """Root + sub LM roles from FLEET_CLEAN_* / DSPY_* settings (no litellm import)."""
    api_key = _resolve_api_key(settings)
    if not api_key:
        msg = (
            "LLM API key not configured "
            "(FLEET_CLEAN_LLM_API_KEY, DSPY_LLM_API_KEY, or OPENAI_API_KEY)"
        )
        raise RuntimeError(msg)
    base_url = _resolve_base_url(settings)
    max_tokens = _resolve_max_tokens(settings)
    root_name = _resolve_model_name(settings, role="root")
    sub_name = _resolve_model_name(settings, role="sub")
    root = build_lm(
        root_name,
        api_key=api_key,
        base_url=base_url,
        max_tokens=max_tokens,
    )
    sub = build_lm(
        sub_name,
        api_key=api_key,
        base_url=base_url,
        max_tokens=max_tokens,
    )
    return RLMModelBundle(root_lm=root, sub_lm=sub)
