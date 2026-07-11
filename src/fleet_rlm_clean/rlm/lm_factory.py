"""Build RLMModelBundle from settings via stock dspy.LM (no direct litellm)."""

from __future__ import annotations

import os
from typing import Any

import dspy

from fleet_rlm_clean.config import Settings
from fleet_rlm_clean.rlm.model_bundle import RLMModelBundle


def _resolve_api_key(settings: Settings) -> str | None:
    if settings.llm_api_key is not None:
        value = settings.llm_api_key.get_secret_value()
        if value:
            return value
    for env_name in ("FLEET_CLEAN_LLM_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"):
        value = os.environ.get(env_name)
        if value:
            return value
    return None


def _resolve_base_url(settings: Settings) -> str | None:
    if settings.llm_base_url:
        return settings.llm_base_url
    for env_name in ("FLEET_CLEAN_LLM_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE"):
        value = os.environ.get(env_name)
        if value:
            return value
    return None


def build_lm(model: str, *, api_key: str | None, base_url: str | None = None) -> dspy.LM:
    """Construct a stock dspy.LM for the given model id."""
    kwargs: dict[str, Any] = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["api_base"] = base_url
    return dspy.LM(model, **kwargs)


def build_model_bundle(settings: Settings) -> RLMModelBundle:
    """Root + sub LM roles from FLEET_CLEAN_* settings (and common env fallbacks)."""
    api_key = _resolve_api_key(settings)
    if not api_key:
        msg = "LLM API key not configured (FLEET_CLEAN_LLM_API_KEY or OPENAI_API_KEY)"
        raise RuntimeError(msg)
    base_url = _resolve_base_url(settings)
    root = build_lm(settings.root_model, api_key=api_key, base_url=base_url)
    sub = build_lm(settings.sub_model, api_key=api_key, base_url=base_url)
    return RLMModelBundle(root_lm=root, sub_lm=sub)
