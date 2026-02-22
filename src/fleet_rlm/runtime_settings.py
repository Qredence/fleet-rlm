"""Shared runtime settings utilities for bridge and HTTP surfaces."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from dotenv import set_key

DEFAULT_SETTINGS_KEYS: tuple[str, ...] = (
    "DSPY_LM_MODEL",
    "DSPY_LLM_API_KEY",
    "DSPY_LM_API_BASE",
    "DSPY_LM_MAX_TOKENS",
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
)

RUNTIME_SETTINGS_KEYS: tuple[str, ...] = (
    "DSPY_LM_MODEL",
    "DSPY_LLM_API_KEY",
    "DSPY_LM_API_BASE",
    "DSPY_LM_MAX_TOKENS",
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
    "SECRET_NAME",
    "VOLUME_NAME",
)

RUNTIME_SETTINGS_ALLOWLIST: frozenset[str] = frozenset(RUNTIME_SETTINGS_KEYS)

_SECRET_KEYS = {
    "DSPY_LLM_API_KEY",
    "DSPY_LM_API_KEY",
    "MODAL_TOKEN_SECRET",
}

_NON_SECRET_KEYS = {
    "DSPY_LM_MODEL",
    "DSPY_LM_API_BASE",
    "DSPY_LM_MAX_TOKENS",
    "SECRET_NAME",
    "VOLUME_NAME",
}

_SENSITIVE_KEY_MARKERS = (
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "TOKEN",
    "API_KEY",
    "PRIVATE_KEY",
    "ACCESS_KEY",
)


def resolve_env_path() -> Path:
    """Resolve the project-local .env path by searching for pyproject.toml."""
    for parent in [Path.cwd(), *Path.cwd().parents]:
        if (parent / "pyproject.toml").exists():
            return parent / ".env"
    return Path.cwd() / ".env"


def requested_keys(
    params: Mapping[str, Any] | None,
    *,
    default_keys: tuple[str, ...] = DEFAULT_SETTINGS_KEYS,
) -> list[str]:
    payload = params or {}
    keys = payload.get("keys")
    if isinstance(keys, list) and keys:
        return [str(item) for item in keys]
    return list(default_keys)


def mask_secret(value: str) -> str:
    """Mask a secret value while preserving tiny prefix/suffix context."""
    if not value:
        return ""
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}...{value[-2:]}"


def should_mask_key(key: str) -> bool:
    """Return True when a key should be treated as sensitive."""
    normalized = key.upper()
    if normalized in _NON_SECRET_KEYS:
        return False
    return key in _SECRET_KEYS or any(
        marker in normalized for marker in _SENSITIVE_KEY_MARKERS
    )


def get_settings_snapshot(
    *,
    keys: list[str],
    extra_values: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """Build a masked settings snapshot for the requested keys."""
    extras = dict(extra_values or {})
    raw_values: dict[str, str] = {}

    for key in keys:
        env_value = os.environ.get(key)
        if env_value is not None:
            raw_values[key] = env_value
            continue
        extra = extras.get(key)
        raw_values[key] = "" if extra is None else str(extra)

    masked_values = {
        key: mask_secret(value) if should_mask_key(key) else value
        for key, value in raw_values.items()
    }
    env_path = resolve_env_path()

    return {
        "env_path": str(env_path),
        "keys": keys,
        "values": masked_values,
        "masked_values": masked_values,
    }


def normalize_updates(
    updates: Mapping[str, Any],
    *,
    allowlist: frozenset[str] | None = None,
) -> dict[str, str]:
    """Normalize update payload into string key/value pairs."""
    normalized: dict[str, str] = {}
    invalid_keys: list[str] = []

    for key, value in updates.items():
        k = str(key).strip()
        if not k or value is None:
            continue
        if allowlist is not None and k not in allowlist:
            invalid_keys.append(k)
            continue
        normalized[k] = str(value)

    if invalid_keys:
        allowed = ", ".join(sorted(allowlist or ()))
        invalid = ", ".join(sorted(invalid_keys))
        raise ValueError(
            f"Unsupported settings key(s): {invalid}. Allowed keys: {allowed}"
        )

    return normalized


def apply_env_updates(
    *, updates: Mapping[str, str], env_path: Path | None = None
) -> dict[str, Any]:
    """Apply updates to .env and current process environment."""
    target = env_path or resolve_env_path()
    if not updates:
        return {"updated": [], "env_path": str(target)}

    target.touch(exist_ok=True)
    for key, value in updates.items():
        set_key(str(target), key, value)
        os.environ[key] = value

    return {"updated": sorted(updates.keys()), "env_path": str(target)}
