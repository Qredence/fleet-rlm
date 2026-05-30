"""Native Daytona configuration helpers and SDK client/error utilities."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from dotenv import dotenv_values

from fleet_rlm.integrations.config.runtime_settings import resolve_env_path

from .errors import DaytonaConfigError
from .models import SandboxLmRuntimeConfig


@dataclass(slots=True)
class ResolvedDaytonaConfig:
    """Explicit Daytona configuration resolved from env and .env files."""

    api_key: str
    api_url: str
    target: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


def _load_env_sources() -> dict[str, str]:
    file_values: dict[str, str] = {}
    env_path = resolve_env_path(start_paths=[Path.cwd()])
    candidates = (env_path, env_path.with_name(".env.local"))
    for candidate in candidates:
        if not candidate.exists():
            continue
        values = dotenv_values(candidate)
        for key, value in values.items():
            if key and value is not None:
                file_values[str(key)] = str(value)

    merged: dict[str, str] = {str(key): str(value) for key, value in os.environ.items()}
    app_env = (os.getenv("APP_ENV") or "local").strip().lower()
    if app_env == "local":
        merged.update(file_values)
    else:
        merged = dict(file_values) | merged
    return merged


def resolve_daytona_config(
    env: Mapping[str, str] | None = None,
) -> ResolvedDaytonaConfig:
    """Resolve native Daytona configuration from environment values."""

    values = dict(env) if env is not None else _load_env_sources()

    api_key = values.get("DAYTONA_API_KEY", "").strip()
    api_url = values.get("DAYTONA_API_URL", "").strip()
    target = values.get("DAYTONA_TARGET", "").strip() or None

    if not api_key:
        raise DaytonaConfigError("Missing DAYTONA_API_KEY. Set DAYTONA_API_KEY before using Daytona commands.")
    if not api_url:
        raise DaytonaConfigError("Missing DAYTONA_API_URL. Set DAYTONA_API_URL before using Daytona commands.")

    return ResolvedDaytonaConfig(
        api_key=api_key,
        api_url=api_url,
        target=target,
    )


def resolve_daytona_lm_runtime_config(
    env: Mapping[str, str] | None = None,
) -> SandboxLmRuntimeConfig:
    """Resolve the LM config that Daytona sandboxes should boot locally."""

    values = dict(env) if env is not None else _load_env_sources()
    api_key = values.get("DSPY_LLM_API_KEY", "").strip() or values.get("DSPY_LM_API_KEY", "").strip()
    model = values.get("DSPY_LM_MODEL", "").strip()
    if not model or not api_key:
        raise DaytonaConfigError(
            "Missing DSPY_LM_MODEL or DSPY_LLM_API_KEY / DSPY_LM_API_KEY. "
            "Daytona self-orchestrated runs require sandbox-local LM config."
        )

    api_base = values.get("DSPY_LM_API_BASE", "").strip() or None
    small_model = values.get("DSPY_LM_SMALL_MODEL", "").strip()
    delegate_model = small_model or None
    delegate_api_key = api_key if delegate_model else None
    delegate_api_base = api_base if delegate_model else None

    return SandboxLmRuntimeConfig.from_raw(
        {
            "model": model,
            "api_key": api_key,
            "api_base": api_base,
            "max_tokens": values.get("DSPY_LM_MAX_TOKENS", ""),
            "delegate_model": delegate_model,
            "delegate_api_key": delegate_api_key if delegate_model else None,
            "delegate_api_base": delegate_api_base if delegate_model else None,
        }
    )


# ---------------------------------------------------------------------------
# SDK client construction and error helpers
# ---------------------------------------------------------------------------

_RESOURCE_ERROR_STATUS_CODES = frozenset({400, 409, 429})
_RESOURCE_ERROR_KEYWORDS = frozenset(
    {
        "capacity",
        "insufficient",
        "limit",
        "precondition",
        "quota",
        "rate limit",
        "resource",
        "too many",
    }
)


@dataclass(frozen=True, slots=True)
class DaytonaSdkErrorClassification:
    """Normalized classification for provider SDK exceptions."""

    status_code: int | None
    kind: str
    message: str

    @property
    def is_resource_or_quota_error(self) -> bool:
        return self.kind == "resource_or_quota"


def daytona_import_error(exc: ImportError) -> RuntimeError:
    """Build the standard Daytona SDK missing-dependency error."""
    return RuntimeError(
        "Daytona SDK is not available. Install dependencies with `uv sync` "
        "and configure DAYTONA_API_KEY / DAYTONA_API_URL before using Daytona "
        "commands. See https://www.daytona.io/docs/en/python-sdk/"
    )


def build_daytona_client(config: ResolvedDaytonaConfig) -> Any:
    """Build a sync Daytona client lazily to keep imports light."""
    try:
        from daytona import Daytona, DaytonaConfig
    except ImportError as exc:  # pragma: no cover - environment specific
        raise daytona_import_error(exc) from exc
    return Daytona(
        DaytonaConfig(
            api_key=config.api_key,
            api_url=config.api_url.rstrip("/"),
            target=config.target,
        )
    )


def _extract_status_code(value: Any) -> int | None:
    for attr in ("status", "status_code", "code"):
        raw = getattr(value, attr, None)
        if raw in (None, ""):
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    response = getattr(value, "response", None)
    if response is not None and response is not value:
        return _extract_status_code(response)
    return None


def _exception_message(exc: BaseException) -> str:
    parts: list[str] = []
    for attr in ("message", "reason", "body", "text", "detail"):
        raw = getattr(exc, attr, None)
        if raw not in (None, ""):
            parts.append(str(raw))
    text = str(exc)
    if text:
        parts.append(text)
    return " | ".join(dict.fromkeys(parts)) or exc.__class__.__name__


def classify_daytona_sdk_error(exc: BaseException) -> DaytonaSdkErrorClassification:
    """Classify Daytona SDK exceptions across SDK status-code changes.

    Daytona 0.170+ moved several quota/resource/precondition failures to HTTP
    400, while older behavior and some proxies may still surface 409/429.
    Treat those statuses plus provider keywords as one diagnostic class so the
    runtime can give stable guidance across SDK releases.
    """
    status_code = _extract_status_code(exc)
    message = _exception_message(exc)
    lowered = message.lower()
    has_resource_keyword = any(keyword in lowered for keyword in _RESOURCE_ERROR_KEYWORDS)
    kind = "provider_error"
    if status_code == 429 or (status_code in _RESOURCE_ERROR_STATUS_CODES and has_resource_keyword):
        kind = "resource_or_quota"
    return DaytonaSdkErrorClassification(
        status_code=status_code,
        kind=kind,
        message=message,
    )


def format_daytona_sdk_error(exc: BaseException) -> str:
    """Return a stable, operator-friendly Daytona SDK error string."""
    classification = classify_daytona_sdk_error(exc)
    status = f"HTTP {classification.status_code}" if classification.status_code is not None else "unknown HTTP status"
    if classification.is_resource_or_quota_error:
        return f"Daytona resource/quota/precondition failure ({status}): {classification.message}"
    return f"Daytona provider failure ({status}): {classification.message}"


__all__ = [
    "DaytonaConfigError",
    "DaytonaSdkErrorClassification",
    "ResolvedDaytonaConfig",
    "build_daytona_client",
    "classify_daytona_sdk_error",
    "daytona_import_error",
    "format_daytona_sdk_error",
    "resolve_daytona_config",
    "resolve_daytona_lm_runtime_config",
]
