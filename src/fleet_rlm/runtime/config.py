"""Configuration utilities for the shared DSPy + Daytona runtime.

This module handles environment configuration, including loading `.env` files,
finding project roots, and keeping DSPy adapter/model setup lightweight at
import time.
"""

from __future__ import annotations

from contextlib import nullcontext
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

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


def load_posthog_settings_from_env() -> dict[str, object]:
    """Load PostHog analytics settings from environment variables."""
    from fleet_rlm.integrations.observability.config import (
        PROJECT_POSTHOG_DEFAULT_API_KEY,
        PROJECT_POSTHOG_DEFAULT_HOST,
    )

    api_key = (
        os.getenv("POSTHOG_API_KEY") or ""
    ).strip() or PROJECT_POSTHOG_DEFAULT_API_KEY
    host = (os.getenv("POSTHOG_HOST") or "").strip() or PROJECT_POSTHOG_DEFAULT_HOST
    enabled_raw = os.getenv("POSTHOG_ENABLED")
    return {
        "enabled": _env_bool(enabled_raw, default=bool(api_key)),
        "api_key": api_key,
        "host": host,
        "flush_interval": float(os.getenv("POSTHOG_FLUSH_INTERVAL", "10.0")),
        "flush_at": max(1, int(os.getenv("POSTHOG_FLUSH_AT", "10"))),
        "enable_dspy_optimization": _env_bool(
            os.getenv("POSTHOG_ENABLE_DSPY_OPTIMIZATION"), default=False
        ),
        "input_truncation_chars": max(
            1, int(os.getenv("POSTHOG_INPUT_TRUNCATION", "10000"))
        ),
        "output_truncation_chars": max(
            1, int(os.getenv("POSTHOG_OUTPUT_TRUNCATION", "5000"))
        ),
        "redact_sensitive": _env_bool(
            os.getenv("POSTHOG_REDACT_SENSITIVE"), default=True
        ),
        "distinct_id": os.getenv("POSTHOG_DISTINCT_ID") or None,
    }


def configure_posthog_analytics_from_env() -> object | None:
    """Best-effort env-driven analytics setup (non-blocking and idempotent)."""
    settings = load_posthog_settings_from_env()
    if not settings.get("enabled") or not settings.get("api_key"):
        return None

    try:
        from fleet_rlm.integrations.observability import configure_analytics
    except ImportError:
        return None

    try:
        return configure_analytics(
            api_key=settings["api_key"]
            if isinstance(settings["api_key"], str)
            else None,
            host=settings["host"]
            if isinstance(settings["host"], str)
            else "https://eu.i.posthog.com",
            distinct_id=settings["distinct_id"]
            if isinstance(settings["distinct_id"], str)
            else None,
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


def _normalize_adapter_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower().replace("-", "_")
    if normalized in _DISABLED_ADAPTER_NAMES:
        return None
    if normalized in {"json", "chat"}:
        return normalized
    raise ValueError(
        "Unsupported DSPy adapter name. Choose one of: chat, json, auto, none, off."
    )


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


def _build_lm(
    *,
    model: str,
    api_key: str,
    api_base: str | None = None,
    max_tokens: int,
) -> Any:
    return _import_dspy().LM(
        model,
        api_base=api_base,
        api_key=api_key,
        max_tokens=max_tokens,
    )


def _planner_lm_kwargs(
    *,
    model_name: str | None = None,
) -> dict[str, Any] | None:
    api_key = os.environ.get("DSPY_LLM_API_KEY") or os.environ.get("DSPY_LM_API_KEY")
    model = model_name or os.environ.get("DSPY_LM_MODEL")
    if not model or not api_key:
        return None

    return {
        "model": model,
        "api_key": api_key,
        "api_base": os.environ.get("DSPY_LM_API_BASE"),
        "max_tokens": _resolve_max_tokens(os.environ.get("DSPY_LM_MAX_TOKENS")),
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
        return None

    api_key = (
        os.environ.get("DSPY_DELEGATE_LM_API_KEY")
        or default_api_key
        or os.environ.get("DSPY_LLM_API_KEY")
        or os.environ.get("DSPY_LM_API_KEY")
    )
    if not api_key:
        logger.warning(
            "Delegate LM model is configured but no API key is available; using planner fallback."
        )
        return None

    return {
        "model": model,
        "api_key": api_key,
        "api_base": (
            os.environ.get("DSPY_DELEGATE_LM_API_BASE")
            or default_api_base
            or os.environ.get("DSPY_LM_API_BASE")
        ),
        "max_tokens": _resolve_max_tokens(default_max_tokens),
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
            os.environ.get(
                "DSPY_STRUCTURED_OUTPUT_ADAPTER_USE_NATIVE_FUNCTION_CALLING"
            ),
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
    planner_lm = _build_lm(**planner_lm_kwargs)
    configure_kwargs: dict[str, Any] = {"lm": planner_lm}
    adapter = get_default_dspy_adapter_from_env(env_file=env_file)
    if adapter is not None:
        configure_kwargs["adapter"] = adapter
    dspy.configure(**configure_kwargs)
    return True


def get_planner_lm_from_env(
    *, env_file: Path | None = None, model_name: str | None = None
) -> dspy.LM | None:
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
    return _build_lm(**planner_lm_kwargs)


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
        return _build_lm(**delegate_lm_kwargs)
    except Exception as exc:
        logger.warning(
            "Failed to initialize delegate LM (%s); using planner fallback.",
            type(exc).__name__,
        )
        return None
