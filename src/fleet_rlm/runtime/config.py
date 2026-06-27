"""Configuration utilities for the shared DSPy + Daytona runtime.

This module handles environment configuration, including loading `.env` files,
finding project roots, and keeping DSPy adapter/model setup lightweight at
import time.
"""

from __future__ import annotations

import logging
import os
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from dotenv import load_dotenv

from fleet_rlm.integrations.config._env_utils import env_bool as _env_bool
from fleet_rlm.integrations.config.runtime_settings import resolve_env_path

if TYPE_CHECKING:
    import dspy

logger = logging.getLogger(__name__)


class _LazyDSPYProxy:
    """Lazily resolve DSPy while preserving a monkeypatchable module seam."""

    def __getattr__(self, name: str):
        module = _import_dspy_module()
        return getattr(module, name)


def _import_dspy_module():
    import dspy as dspy_module

    return dspy_module


dspy: _LazyDSPYProxy = _LazyDSPYProxy()


STRUCTURE_SENSITIVE_RUNTIME_MODULES: frozenset[str] = frozenset(
    {
        "grounded_answer",
        "memory_tree",
        "memory_action_intent",
        "memory_structure_migration_plan",
        "clarification_questions",
        "triage_incident_logs",
    }
)

_DISABLED_ADAPTER_NAMES: frozenset[str] = frozenset({"", "auto", "none", "off"})


def configure_posthog_analytics_from_env() -> object | None:
    """Best-effort env-driven analytics setup (non-blocking and idempotent).

    Uses :meth:`PostHogConfig.from_env` (the canonical PostHog env loader in
    ``integrations/observability/config.py``) instead of duplicating the
    ``POSTHOG_*`` env-reading logic here.
    """
    from fleet_rlm.integrations.observability.config import PostHogConfig

    settings = PostHogConfig.from_env()
    if not settings.enabled or not settings.api_key:
        return None

    try:
        from fleet_rlm.integrations.observability import configure_analytics
    except ImportError:
        return None

    try:
        return configure_analytics(
            api_key=settings.api_key,
            host=settings.host,
            distinct_id=os.getenv("POSTHOG_DISTINCT_ID") or None,
            enabled=True,
        )
    except Exception:
        logger.debug("posthog_analytics_configure_failed", exc_info=True)
        return None


def _prepare_env(*, env_file: Path | None = None) -> None:
    """Load env defaults for LM configuration helpers."""
    dotenv_path = env_file
    if dotenv_path is None:
        dotenv_path = resolve_env_path(start_paths=[Path.cwd()])

    app_env = (os.getenv("APP_ENV") or "local").strip().lower()
    load_dotenv(dotenv_path, override=app_env == "local")


def _import_dspy() -> Any:
    return dspy


def configure_dspy_cache_security(dspy_module: Any | None = None) -> None:
    """Keep DSPy's disk-backed pickle cache disabled unless explicitly enabled."""
    module = dspy_module or _import_dspy()
    configure_cache = getattr(module, "configure_cache", None)
    if configure_cache is None:
        return

    enable_disk_cache = _env_bool(os.getenv("FLEET_RLM_ENABLE_DSPY_DISK_CACHE"), default=False)
    cache_kwargs: dict[str, Any] = {
        "enable_disk_cache": enable_disk_cache,
        "enable_memory_cache": True,
        "restrict_pickle": True,
    }
    try:
        configure_cache(**cache_kwargs)
    except TypeError:
        cache_kwargs.pop("restrict_pickle")
        configure_cache(**cache_kwargs)


def _normalize_adapter_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower().replace("-", "_")
    if normalized in _DISABLED_ADAPTER_NAMES:
        return None
    if normalized in {"json", "chat"}:
        return normalized
    raise ValueError("Unsupported DSPy adapter name. Choose one of: chat, json, auto, none, off.")


def _build_adapter(
    adapter_name: str | None,
    *,
    use_native_function_calling: bool = False,
) -> Any | None:
    normalized = _normalize_adapter_name(adapter_name)
    if normalized is None:
        return None

    dspy = _import_dspy()
    if normalized == "json":
        return dspy.JSONAdapter(use_native_function_calling=use_native_function_calling)
    return dspy.ChatAdapter(use_native_function_calling=use_native_function_calling)


def _resolve_max_tokens(value: int | str | None, *, default: int = 64000) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


# Normalized LM API (dspy.ai/community/normalized-lm-api-migration):
# fleet-rlm uses a custom BaseLM subclass (ResponseAPILM) for OpenAI providers
# with the typed_lm forward contract and OpenAI Response API. For non-OpenAI
# providers (anthropic, google, openai_compatible, etc.), we use stock dspy.LM
# (litellm-backed). The ResponseAPILM is defined in runtime/lm.py.
# Always invoke the LM as lm(...) (never lm.forward(...)).
def _build_lm(
    *,
    model: str,
    api_key: str,
    api_base: str | None = None,
    max_tokens: int,
    custom_provider: str | None = None,
) -> Any:
    """Build an LM instance. Uses ResponseAPILM for OpenAI, stock dspy.LM for others."""
    from fleet_rlm.runtime.lm import ResponseAPILM

    # Check if this is an OpenAI provider
    is_openai = (
        model.startswith("openai/") or custom_provider == "openai" or (api_base and "openai" in api_base.lower())
    )

    if is_openai:
        # Strip provider prefix (e.g., "openai/gemini-3.5-flash" -> "gemini-3.5-flash")
        # for OpenAI-compatible APIs that don't expect the prefix
        model_name = model.split("/", 1)[1] if "/" in model else model

        # Use custom ResponseAPILM with OpenAI Response API
        return ResponseAPILM(
            model=model_name,
            api_key=api_key,
            api_base=api_base,
            max_tokens=max_tokens,
            custom_llm_provider=custom_provider,
        )
    else:
        # Fall back to stock dspy.LM (litellm-backed) for non-OpenAI providers
        extra: dict[str, Any] = {}
        # Opt-in provider hint. When callers set ``DSPY_LM_CUSTOM_PROVIDER`` (or the
        # delegate equivalent) we forward it so litellm routes bare model names
        # against the custom api_base with the right wire format. Without an
        # explicit hint we leave provider detection to litellm so non-OpenAI
        # compatible endpoints (e.g. Anthropic) keep working.
        if custom_provider:
            extra["custom_llm_provider"] = custom_provider
        return _import_dspy().LM(
            model,
            api_base=api_base,
            api_key=api_key,
            max_tokens=max_tokens,
            **extra,
        )


def _planner_lm_kwargs(
    *,
    model_name: str | None = None,
) -> dict[str, Any] | None:
    api_key = os.environ.get("DSPY_LLM_API_KEY") or os.environ.get("DSPY_LM_API_KEY")
    model = model_name or os.environ.get("DSPY_LM_MODEL")
    if not model:
        logger.warning(
            "No planner LM model configured (DSPY_LM_MODEL is unset/empty); "
            "returning None. Set DSPY_LM_MODEL or configure a BYOK LLM profile."
        )
        return None
    if not api_key:
        logger.warning(
            "Planner LM model '%s' is configured but no API key is available "
            "(DSPY_LLM_API_KEY/DSPY_LM_API_KEY unset); returning None.",
            model,
        )
        return None

    custom_provider = (os.environ.get("DSPY_LM_CUSTOM_PROVIDER") or "").strip() or None
    return {
        "model": model,
        "api_key": api_key,
        "api_base": os.environ.get("DSPY_LM_API_BASE"),
        "max_tokens": _resolve_max_tokens(os.environ.get("DSPY_LM_MAX_TOKENS")),
        "custom_provider": custom_provider,
    }


def _delegate_lm_kwargs(
    *,
    model_name: str | None = None,
    default_api_key: str | None = None,
    default_api_base: str | None = None,
    default_max_tokens: int | str | None = None,
) -> dict[str, Any] | None:
    model = model_name or os.environ.get("DSPY_DELEGATE_LM_MODEL")
    if not model:
        logger.warning(
            "No delegate LM model configured (DSPY_DELEGATE_LM_MODEL is unset/empty); "
            "returning None. Callers should fall back to the planner LM."
        )
        return None

    api_key = (
        os.environ.get("DSPY_DELEGATE_LM_API_KEY")
        or default_api_key
        or os.environ.get("DSPY_LLM_API_KEY")
        or os.environ.get("DSPY_LM_API_KEY")
    )
    if not api_key:
        logger.warning("Delegate LM model is configured but no API key is available; using planner fallback.")
        return None

    custom_provider = (os.environ.get("DSPY_DELEGATE_LM_CUSTOM_PROVIDER") or "").strip() or None
    return {
        "model": model,
        "api_key": api_key,
        "api_base": (
            os.environ.get("DSPY_DELEGATE_LM_API_BASE") or default_api_base or os.environ.get("DSPY_LM_API_BASE")
        ),
        "max_tokens": _resolve_max_tokens(default_max_tokens),
        "custom_provider": custom_provider,
    }


def get_default_dspy_adapter_from_env(*, env_file: Path | None = None) -> Any | None:
    """Return the optional default adapter for non-runtime-module DSPy contexts."""
    _prepare_env(env_file=env_file)
    return _build_adapter(
        os.environ.get("DSPY_ADAPTER"),
        use_native_function_calling=_env_bool(
            os.environ.get("DSPY_ADAPTER_USE_NATIVE_FUNCTION_CALLING"),
            default=False,
        ),
    )


def get_runtime_module_adapter(
    module_name: str | None,
    *,
    env_file: Path | None = None,
) -> Any | None:
    """Return the adapter for structure-sensitive runtime modules.

    By default these modules use ``JSONAdapter`` for clearer structured output.
    Set ``DSPY_STRUCTURED_OUTPUT_ADAPTER=chat`` (or ``none``/``off``) to override.
    """
    if module_name not in STRUCTURE_SENSITIVE_RUNTIME_MODULES:
        return None

    _prepare_env(env_file=env_file)
    return _build_adapter(
        os.environ.get("DSPY_STRUCTURED_OUTPUT_ADAPTER", "json"),
        use_native_function_calling=_env_bool(
            os.environ.get("DSPY_STRUCTURED_OUTPUT_ADAPTER_USE_NATIVE_FUNCTION_CALLING"),
            default=False,
        ),
    )


def build_dspy_context(
    *,
    lm: Any | None = None,
    module_name: str | None = None,
    adapter: Any | None = None,
    allow_tool_async_sync_conversion: bool | None = None,
) -> Any:
    """Build a ``dspy.context`` with the configured LM/adapter strategy.

    When *module_name* is one of the structure-sensitive runtime modules, this
    applies the configured structured-output adapter. For ordinary call sites it
    uses the optional default adapter configured via ``DSPY_ADAPTER``.
    """
    kwargs: dict[str, Any] = {}
    if lm is not None:
        kwargs["lm"] = lm

    resolved_adapter = adapter
    if resolved_adapter is None:
        if module_name is not None:
            resolved_adapter = get_runtime_module_adapter(module_name)
        else:
            resolved_adapter = get_default_dspy_adapter_from_env()
    if resolved_adapter is not None:
        kwargs["adapter"] = resolved_adapter

    if allow_tool_async_sync_conversion is not None:
        kwargs["allow_tool_async_sync_conversion"] = allow_tool_async_sync_conversion

    if not kwargs:
        return nullcontext()

    dspy = _import_dspy()
    configure_dspy_cache_security(dspy)
    return dspy.context(**kwargs)


def configure_planner_from_env(*, env_file: Path | None = None) -> bool:
    """Configure DSPy's planner LM from environment variables.

    Loads environment variables from a .env file (if found) and configures
    DSPy with a language model based on the loaded configuration.

    Required environment variables:
        - DSPY_LM_MODEL: The model identifier (e.g., "openai/gemini/gemini-3.1-pro-preview")
        - DSPY_LLM_API_KEY or DSPY_LM_API_KEY: API key for the model provider

    Optional environment variables:
        - DSPY_LM_API_BASE: Custom API base URL
        - DSPY_LM_MAX_TOKENS: Maximum tokens for generation (default: 16000)

    Args:
        env_file: Optional path to a specific .env file. If not provided,
            searches for .env in the project root (directory containing
            pyproject.toml) or current working directory.

    Returns:
        True if the planner was successfully configured, False if required
        environment variables (DSPY_LM_MODEL and API key) are not set.

    Example:
        >>> from fleet_rlm import configure_planner_from_env
        >>> success = configure_planner_from_env()
        >>> if not success:
        ...     print("Failed to configure planner - check environment variables")
    """

    _prepare_env(env_file=env_file)

    planner_lm_kwargs = _planner_lm_kwargs()
    if planner_lm_kwargs is None:
        return False

    dspy = _import_dspy()
    configure_dspy_cache_security(dspy)
    planner_lm = _build_lm(**planner_lm_kwargs)
    configure_kwargs: dict[str, Any] = {"lm": planner_lm}
    adapter = get_default_dspy_adapter_from_env(env_file=env_file)
    if adapter is not None:
        configure_kwargs["adapter"] = adapter
    dspy.configure(**configure_kwargs)
    return True


def get_planner_lm_from_env(*, env_file: Path | None = None, model_name: str | None = None) -> dspy.LM | None:
    """Create and return a DSPy LM from environment.

    This is the async-safe version of configure_planner_from_env(). It creates
    and returns the LM object without calling dspy.configure(), allowing the
    caller to use dspy.context() for thread-local configuration instead.

    Args:
        env_file: Optional path to a specific .env file.
        model_name: Optional explicit model identifier to use, overriding environment.

    Returns:
        A configured dspy.LM instance if configuration is available, None otherwise.
    """
    _prepare_env(env_file=env_file)
    planner_lm_kwargs = _planner_lm_kwargs(model_name=model_name)
    if planner_lm_kwargs is None:
        return None
    configure_dspy_cache_security()
    return _build_lm(**planner_lm_kwargs)


LmRole = Literal["planner", "delegate", "reflection", "judge"]


def resolve_lm(
    role: LmRole = "planner",
    *,
    env_file: Path | None = None,
    model_name: str | None = None,
) -> dspy.LM | None:
    """Resolve a DSPy LM for a runtime role without mutating global settings.

    This is the single LM-resolution entrypoint; callers scope the result via
    ``build_dspy_context(lm=...)`` rather than ``dspy.configure``.

    Roles:
        - ``planner``: the primary planner LM from ``DSPY_LM_MODEL``.
        - ``delegate``: the optional stronger/cheaper delegate LM from
          ``DSPY_DELEGATE_LM_MODEL``, falling back to the planner LM.
        - ``reflection``: LM for GEPA's reflection pass — delegate first,
          then planner.
        - ``judge``: deterministic (temperature 0) LM for LLM-judge scoring.
          Requires an explicit *model_name*.

    Returns ``None`` when no configuration is available for the role.
    """
    if role == "planner":
        return get_planner_lm_from_env(env_file=env_file, model_name=model_name)
    if role in ("delegate", "reflection"):
        lm = get_delegate_lm_from_env(
            env_file=env_file,
            model_name=model_name if role == "delegate" else None,
        )
        if lm is not None:
            return lm
        return get_planner_lm_from_env(env_file=env_file)
    if role == "judge":
        if not model_name:
            return None
        dspy = _import_dspy()
        configure_dspy_cache_security(dspy)
        # Use ResponseAPILM for OpenAI providers
        if model_name.startswith("openai/"):
            from fleet_rlm.runtime.lm import ResponseAPILM

            api_key = os.environ.get("DSPY_LLM_API_KEY") or os.environ.get("DSPY_LM_API_KEY") or ""
            return ResponseAPILM(model=model_name, api_key=api_key, temperature=0.0)
        return dspy.LM(model_name, temperature=0.0)
    raise ValueError(f"Unknown LM role: {role!r}")


def _delegate_small_lm_kwargs(
    *,
    model_name: str | None = None,
    default_api_key: str | None = None,
    default_api_base: str | None = None,
    default_max_tokens: int | str | None = None,
) -> dict[str, Any] | None:
    model = model_name or os.environ.get("DSPY_DELEGATE_LM_SMALL_MODEL")
    if not model:
        return None

    api_key = (
        os.environ.get("DSPY_DELEGATE_LM_API_KEY")
        or default_api_key
        or os.environ.get("DSPY_LLM_API_KEY")
        or os.environ.get("DSPY_LM_API_KEY")
    )
    if not api_key:
        logger.warning("Small delegate LM model is configured but no API key is available; using delegate fallback.")
        return None

    custom_provider = (os.environ.get("DSPY_DELEGATE_LM_CUSTOM_PROVIDER") or "").strip() or None
    return {
        "model": model,
        "api_key": api_key,
        "api_base": (
            os.environ.get("DSPY_DELEGATE_LM_API_BASE") or default_api_base or os.environ.get("DSPY_LM_API_BASE")
        ),
        "max_tokens": _resolve_max_tokens(
            default_max_tokens if default_max_tokens is not None else os.environ.get("DSPY_DELEGATE_LM_MAX_TOKENS")
        ),
        "custom_provider": custom_provider,
    }


def get_delegate_lm_from_env(
    *,
    env_file: Path | None = None,
    model_name: str | None = None,
    default_api_key: str | None = None,
    default_api_base: str | None = None,
    default_max_tokens: int | None = None,
) -> dspy.LM | None:
    """Create and return an optional delegate DSPy LM from environment.

    Resolution policy:
    - model: explicit ``model_name`` -> ``DSPY_DELEGATE_LM_MODEL`` -> ``None``
    - api key: ``DSPY_DELEGATE_LM_API_KEY`` -> ``default_api_key`` -> planner key envs
    - api base: ``DSPY_DELEGATE_LM_API_BASE`` -> ``default_api_base`` -> planner base env

    This helper is intentionally best-effort and returns ``None`` on missing
    inputs or init failures so callers can fall back to the parent planner LM.
    """
    _prepare_env(env_file=env_file)
    delegate_lm_kwargs = _delegate_lm_kwargs(
        model_name=model_name,
        default_api_key=default_api_key,
        default_api_base=default_api_base,
        default_max_tokens=default_max_tokens
        if default_max_tokens is not None
        else os.environ.get("DSPY_LM_MAX_TOKENS"),
    )
    if delegate_lm_kwargs is None:
        return None
    try:
        configure_dspy_cache_security()
        return _build_lm(**delegate_lm_kwargs)
    except Exception as exc:
        logger.warning(
            "Failed to initialize delegate LM (%s); using planner fallback.",
            type(exc).__name__,
        )
        return None


def get_delegate_small_lm_from_env(
    *,
    env_file: Path | None = None,
    model_name: str | None = None,
    default_api_key: str | None = None,
    default_api_base: str | None = None,
    default_max_tokens: int | None = None,
) -> dspy.LM | None:
    """Create and return an optional small delegate DSPy LM from environment."""
    _prepare_env(env_file=env_file)
    delegate_small_lm_kwargs = _delegate_small_lm_kwargs(
        model_name=model_name,
        default_api_key=default_api_key,
        default_api_base=default_api_base,
        default_max_tokens=default_max_tokens
        if default_max_tokens is not None
        else os.environ.get("DSPY_DELEGATE_LM_MAX_TOKENS"),
    )
    if delegate_small_lm_kwargs is None:
        return None
    try:
        configure_dspy_cache_security()
        return _build_lm(**delegate_small_lm_kwargs)
    except Exception as exc:
        logger.warning(
            "Failed to initialize small delegate LM (%s); using delegate fallback.",
            type(exc).__name__,
        )
        return None
