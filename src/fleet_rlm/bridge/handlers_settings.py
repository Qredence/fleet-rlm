"""Settings handlers for bridge frontends."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import set_key

_SECRET_KEYS = {"DSPY_LLM_API_KEY", "DSPY_LM_API_KEY", "MODAL_TOKEN_SECRET"}


def get_settings(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return current settings snapshot and masked values."""
    env_path = _resolve_env_path()
    keys = _requested_keys(params or {})
    values = {key: os.environ.get(key, "") for key in keys}
    masked = {
        key: _mask_secret(value) if key in _SECRET_KEYS else value
        for key, value in values.items()
    }
    return {
        "env_path": str(env_path),
        "keys": keys,
        "values": values,
        "masked_values": masked,
    }


def update_settings(params: dict[str, Any]) -> dict[str, Any]:
    """Apply .env updates and mirror values into current process env."""
    updates = params.get("updates", {})
    if not isinstance(updates, dict):
        raise ValueError("`updates` must be a mapping.")

    normalized = {
        str(key): str(value)
        for key, value in updates.items()
        if str(key).strip() and value is not None
    }
    if not normalized:
        return {"updated": [], "env_path": str(_resolve_env_path())}

    env_path = _resolve_env_path()
    env_path.touch(exist_ok=True)
    for key, value in normalized.items():
        set_key(str(env_path), key, value)
        os.environ[key] = value

    return {"updated": sorted(normalized.keys()), "env_path": str(env_path)}


def _requested_keys(params: dict[str, Any]) -> list[str]:
    keys = params.get("keys")
    if isinstance(keys, list) and keys:
        return [str(item) for item in keys]
    return [
        "DSPY_LM_MODEL",
        "DSPY_LLM_API_KEY",
        "DSPY_LM_API_BASE",
        "DSPY_LM_MAX_TOKENS",
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
    ]


def _resolve_env_path() -> Path:
    for parent in [Path.cwd(), *Path.cwd().parents]:
        if (parent / "pyproject.toml").exists():
            return parent / ".env"
    return Path.cwd() / ".env"


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}...{value[-2:]}"
