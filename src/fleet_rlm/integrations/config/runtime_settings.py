"""Shared runtime settings utilities for bridge and HTTP surfaces."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from dotenv import set_key


@dataclass(frozen=True, slots=True)
class RuntimeSettingDefinition:
    """Single-source metadata for runtime settings surfaces."""

    key: str
    secret: bool = False
    include_in_default_snapshot: bool = True
    editable: bool = True


_RUNTIME_SETTING_DEFINITIONS: tuple[RuntimeSettingDefinition, ...] = (
    RuntimeSettingDefinition("DSPY_LM_MODEL"),
    RuntimeSettingDefinition("DSPY_DELEGATE_LM_MODEL"),
    RuntimeSettingDefinition("DSPY_DELEGATE_LM_SMALL_MODEL"),
    RuntimeSettingDefinition("DSPY_DELEGATE_LM_MAX_TOKENS"),
    RuntimeSettingDefinition("DSPY_LLM_API_KEY", secret=True),
    RuntimeSettingDefinition("DSPY_LM_API_BASE"),
    RuntimeSettingDefinition("DSPY_LM_MAX_TOKENS"),
    RuntimeSettingDefinition("DAYTONA_API_KEY", secret=True),
    RuntimeSettingDefinition("DAYTONA_API_URL"),
    RuntimeSettingDefinition("DAYTONA_TARGET"),
)

_RUNTIME_SETTING_INDEX: dict[str, RuntimeSettingDefinition] = {
    definition.key: definition for definition in _RUNTIME_SETTING_DEFINITIONS
}

DEFAULT_SETTINGS_KEYS: tuple[str, ...] = tuple(
    definition.key
    for definition in _RUNTIME_SETTING_DEFINITIONS
    if definition.include_in_default_snapshot
)

RUNTIME_SETTINGS_KEYS: tuple[str, ...] = tuple(
    definition.key for definition in _RUNTIME_SETTING_DEFINITIONS if definition.editable
)

RUNTIME_SETTINGS_ALLOWLIST: frozenset[str] = frozenset(RUNTIME_SETTINGS_KEYS)

_LEGACY_SECRET_KEYS = frozenset({"DSPY_LM_API_KEY"})
_NON_SECRET_KEYS = frozenset(
    definition.key
    for definition in _RUNTIME_SETTING_DEFINITIONS
    if not definition.secret
)

_SENSITIVE_KEY_MARKERS = (
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "TOKEN",
    "API_KEY",
    "PRIVATE_KEY",
    "ACCESS_KEY",
)


def resolve_env_path(*, start_paths: Sequence[Path] | None = None) -> Path:
    """Resolve the project-local .env path by searching for pyproject.toml.

    Priority order:
    1) Explicit ``FLEET_RLM_ENV_PATH`` override (if set)
    2) Upward search from provided ``start_paths`` (or current working directory)
    3) Fallback to ``<first-start-path>/.env``
    """
    explicit = (os.getenv("FLEET_RLM_ENV_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()

    roots = list(start_paths) if start_paths else [Path.cwd()]
    seen: set[Path] = set()
    for root in roots:
        normalized = root if root.is_dir() else root.parent
        for parent in [normalized, *normalized.parents]:
            if parent in seen:
                continue
            seen.add(parent)
            if (parent / "pyproject.toml").exists():
                return parent / ".env"

    fallback_root = roots[0] if roots else Path.cwd()
    fallback_root = fallback_root if fallback_root.is_dir() else fallback_root.parent
    return fallback_root / ".env"


def _read_env_file_values(path: Path) -> dict[str, str]:
    """Best-effort .env parser used for runtime snapshot precedence."""
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue

        value = value.strip()
        if len(value) >= 2 and (
            (value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")
        ):
            value = value[1:-1]

        values[key] = value

    return values


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
    definition = _RUNTIME_SETTING_INDEX.get(normalized)
    if definition is not None:
        return definition.secret
    if normalized in _NON_SECRET_KEYS:
        return False
    return normalized in _LEGACY_SECRET_KEYS or any(
        marker in normalized for marker in _SENSITIVE_KEY_MARKERS
    )


def _is_masked_secret_round_trip(
    *,
    key: str,
    candidate_value: str,
    current_raw_value: str | None,
) -> bool:
    """Detect masked secret placeholders sent back from runtime settings clients.

    Runtime settings snapshots intentionally return masked values for secret-like
    keys (for example ``sk-...yz``). If a client sends those masked display values
    back during a PATCH call, persisting them would overwrite real credentials.
    """
    if not should_mask_key(key):
        return False

    value = candidate_value.strip()
    if not value:
        return False

    # Common fully-redacted placeholder style (e.g. "***").
    if set(value) == {"*"}:
        return True

    if current_raw_value:
        return value == mask_secret(current_raw_value)

    return False


def get_settings_snapshot(
    *,
    keys: list[str],
    extra_values: Mapping[str, str | None] | None = None,
    env_path: Path | None = None,
) -> dict[str, Any]:
    """Build a masked settings snapshot for the requested keys."""
    extras = dict(extra_values or {})
    resolved_env_path = env_path or resolve_env_path()
    file_values = _read_env_file_values(resolved_env_path)
    raw_values: dict[str, str] = {}

    for key in keys:
        env_value = file_values.get(key)
        if env_value is None:
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
    return {
        "env_path": str(resolved_env_path),
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
    *, updates: Mapping[str, Any], env_path: Path | None = None
) -> dict[str, Any]:
    """Apply updates to .env and current process environment."""
    # Normalize and validate updates against the runtime settings allowlist
    normalized_updates = normalize_updates(
        updates,
        allowlist=RUNTIME_SETTINGS_ALLOWLIST,
    )

    target = env_path or resolve_env_path()
    if not normalized_updates:
        return {"updated": [], "env_path": str(target)}

    current_file_values = _read_env_file_values(target)
    effective_updates: dict[str, str] = {}
    for key, value in normalized_updates.items():
        current_raw_value = current_file_values.get(key)
        if current_raw_value is None:
            current_raw_value = os.environ.get(key)
        if _is_masked_secret_round_trip(
            key=key,
            candidate_value=value,
            current_raw_value=current_raw_value,
        ):
            continue
        effective_updates[key] = value

    if not effective_updates:
        return {"updated": [], "env_path": str(target)}

    target.touch(exist_ok=True)
    for key, value in effective_updates.items():
        set_key(str(target), key, value)
        os.environ[key] = value

    return {"updated": sorted(effective_updates.keys()), "env_path": str(target)}
