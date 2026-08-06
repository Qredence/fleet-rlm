"""Build RLMModelBundle via stock ``dspy.LM`` (normalized LiteLLM-backed API).

Follows https://dspy.ai/api/models/LM/ — model ids are ``provider/model`` strings;
credentials and OpenAI-compatible bases are passed as kwargs (``api_key``,
``api_base``). Does not call litellm directly.
"""

from __future__ import annotations

import os
import re
from enum import StrEnum
from typing import Any, Literal

import dspy

from fleet_rlm.config import LLMRoleSettings, Settings
from fleet_rlm.rlm.model_bundle import RLMModelBundle

# Values that look like secrets/keys must never be treated as base URLs.
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_LEGACY_LLM_API_KEY_ENV = "FLEET_OPENAI_API_KEY"
_MODEL_PROVIDER_SERVICE_HEADER = "Databricks-Model-Provider-Service"


def sanitize_base_url(value: str | None) -> str | None:
    """
    Normalize an HTTP or HTTPS base URL.

    Parameters:
        value (str | None): The URL value to sanitize.

    Returns:
        str | None: The normalized URL without trailing slashes, or `None` for an empty, invalid, or unsupported value.
    """
    if value is None:
        return None
    text = str(value).strip().strip("'\"")
    # Drop inline comments (common when pasting from docs into .env)
    if " #" in text:
        text = text.split(" #", 1)[0].rstrip().strip("'\"")
    if not text or not _URL_RE.match(text):
        return None
    return text.rstrip("/")


def normalize_model_id(model: str) -> str:
    """Ensure LiteLLM-style ``provider/model`` form used by ``dspy.LM``.

    Bare model names get the OpenAI-compatible provider prefix even when the
    model name is gateway-local; the prefix is required by LiteLLM regardless
    of whether a custom ``api_base`` is configured.
    """
    cleaned = (model or "").strip().strip("'\"")
    if not cleaned:
        msg = "model id is required"
        raise ValueError(msg)
    if "/" in cleaned:
        return cleaned
    return f"openai/{cleaned}"


def resolve_role_api_key(settings: Settings, role: LLMRoleSettings) -> str | None:
    """
    Resolve the API key configured for an LLM role.

    Parameters:
        settings (Settings): Application settings containing dotenv values and a fallback API key.
        role (LLMRoleSettings): Role configuration identifying the API-key environment variable.

    Returns:
        str | None: The resolved, stripped API key, or `None` when no key is configured.
    """
    value = os.environ.get(role.api_key_env)
    if value is None:
        value = settings._dotenv_values.get(role.api_key_env)
    value = (value or "").strip()
    if value:
        return value
    # ``llm_api_key`` is retained for programmatic Settings construction in
    # tests and integrations; production policy always names a provider env.
    if role.api_key_env == _LEGACY_LLM_API_KEY_ENV and settings.llm_api_key is not None:
        return settings.llm_api_key.get_secret_value().strip() or None
    return None


def has_llm_credentials(settings: Settings) -> bool:
    """Return whether both explicit LLM roles have a configured secret."""
    return all(resolve_role_api_key(settings, settings.llm_role(role)) for role in ("root", "sub"))


def build_lm(
    model: str,
    *,
    api_key: str | None,
    model_provider_service: str | None = None,
    base_url: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    model_type: str = "chat",
    cache: bool = True,
    num_retries: int = 3,
) -> dspy.LM:
    """Construct a stock ``dspy.LM`` per the public LM constructor contract."""
    model_id = normalize_model_id(model)
    kwargs: dict[str, Any] = {
        "model_type": model_type,
        "cache": cache,
        "num_retries": num_retries,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if model_provider_service:
        kwargs["headers"] = {_MODEL_PROVIDER_SERVICE_HEADER: model_provider_service}
    if base_url:
        kwargs["api_base"] = base_url
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if temperature is not None:
        kwargs["temperature"] = temperature
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort
        # The Databricks AI Gateway accepts this OpenAI-compatible parameter,
        # while LiteLLM's generic OpenAI provider otherwise rejects it.
        kwargs["allowed_openai_params"] = ["reasoning_effort"]
    return dspy.LM(model_id, **kwargs)


def build_model_bundle(settings: Settings) -> RLMModelBundle:
    """Build explicit Root and Sub Model roles from resolved Fleet policy."""

    def build(role: Literal["root", "sub"]) -> dspy.LM:
        policy = settings.llm_role(role)
        api_key = resolve_role_api_key(settings, policy)
        if not api_key:
            raise RuntimeError(f"LLM API key not configured ({policy.api_key_env})")
        return build_lm(
            policy.model,
            api_key=api_key,
            model_provider_service=policy.model_provider_service,
            base_url=sanitize_base_url(policy.base_url),
            max_tokens=policy.max_tokens,
            temperature=policy.temperature,
            reasoning_effort=policy.reasoning_effort,
            cache=policy.cache,
            num_retries=policy.num_retries,
        )

    root = build("root")
    sub = build("sub")
    return RLMModelBundle(root_lm=root, sub_lm=sub)


class LMTier(StrEnum):
    """AI Gateway capability/cost tier for fleet-rlm DSPy modules.

    FRONTIER  Highest capability; reserved for offline optimization (GEPA only).
              Must never be used inside a live Turn transaction.
    WORKER    Primary live-Turn root model. Balanced performance / cost.
    FAST      High-throughput: sub-model analysis and inner-loop iterations.
    """

    FRONTIER = "frontier"
    WORKER = "worker"
    FAST = "fast"


# Databricks AI Gateway path appended to the workspace URL.
# The OpenAI client adds /chat/completions automatically.
_AI_GATEWAY_PATH = "/ai-gateway/openai/v1"

# Ordered by preference within each tier; index 0 is the default.
# Values are Unity Catalog model service names routed by the AI Gateway.
_TIER_MODELS: dict[LMTier, list[str]] = {
    LMTier.FRONTIER: [
        "system.ai.claude-opus-4-8",
        "system.ai.gpt-5-6-sol",
    ],
    LMTier.WORKER: [
        "system.ai.gpt-5-6-terra",
        "system.ai.glm-5-2",
        "system.ai.gpt-5-6-luna",
    ],
    LMTier.FAST: [
        "uscentral.default.deepseek-v4-flash",
        "system.ai.gpt-oss-120b",
        "system.ai.gemini-3-1-flash-lite",
        "uscentral.default.nemotron-3-ultra-free",
        "uscentral.default.qwen3-7-max-2026-05-20",
        "uscentral.default.glm-5-1",
    ],
}


def build_lm_for_tier(
    tier: LMTier,
    *,
    workspace_url: str,
    api_key: str,
    preference: int = 0,
    max_tokens: int | None = None,
    cache: bool = True,
    num_retries: int = 3,
) -> dspy.LM:
    """Build a ``dspy.LM`` for the given tier via the Databricks AI Gateway.

    ``workspace_url`` is the Databricks workspace base URL, e.g.
    ``https://8259565402437752.2.gcp.databricks.com``.  ``preference``
    selects an alternative within the tier (0 = primary default).

    FRONTIER is reserved for offline GEPA optimization.  Live Turn callers
    must use WORKER or FAST only.
    """
    models = _TIER_MODELS[tier]
    model_uc = models[preference % len(models)]
    base = f"{workspace_url.rstrip('/')}{_AI_GATEWAY_PATH}"
    return build_lm(
        model_uc,
        api_key=api_key,
        base_url=base,
        max_tokens=max_tokens,
        cache=cache,
        num_retries=num_retries,
    )
