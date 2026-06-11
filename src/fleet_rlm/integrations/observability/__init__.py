"""Observability surface with lazy exports to avoid startup-time side effects."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .posthog_callback import PostHogLLMCallback

_EXPORTS: dict[str, tuple[str, str]] = {
    "log_trace_feedback": (".mlflow_traces", "log_trace_feedback"),
    "resolve_trace": (".mlflow_traces", "resolve_trace"),
}

__all__ = sorted([*_EXPORTS, "configure_analytics"])


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:  # pragma: no cover - Python import protocol
        raise AttributeError(name) from exc

    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def configure_analytics(
    *,
    api_key: str | None = None,
    host: str = "https://us.i.posthog.com",
    distinct_id: str | None = None,
    enabled: bool | None = None,
) -> PostHogLLMCallback | None:
    """Configure and register a PostHog DSPy callback lazily."""
    import dspy

    from .callback_registry import ensure_dspy_callbacks
    from .config import PostHogConfig
    from .posthog_callback import PostHogLLMCallback

    base = PostHogConfig.from_env()
    resolved_host = base.host if host == "https://us.i.posthog.com" and base.host else host
    config = PostHogConfig(
        enabled=base.enabled if enabled is None else enabled,
        api_key=api_key if api_key is not None else base.api_key,
        host=resolved_host,
        flush_interval=base.flush_interval,
        flush_at=base.flush_at,
        enable_dspy_optimization=base.enable_dspy_optimization,
        input_truncation_chars=base.input_truncation_chars,
        output_truncation_chars=base.output_truncation_chars,
        redact_sensitive=base.redact_sensitive,
    )

    if not config.enabled or not config.api_key:
        return None

    callbacks = list(getattr(dspy.settings, "callbacks", []) or [])
    for callback in callbacks:
        if isinstance(callback, PostHogLLMCallback):
            return callback

    callback = PostHogLLMCallback(config, distinct_id=distinct_id)
    ensure_dspy_callbacks([callback])
    return callback
