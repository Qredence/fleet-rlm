"""Profile and settings persistence for interactive code-chat."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import ProfileConfig

CONFIG_DIR = Path.home() / ".config" / "fleet-rlm"
PROFILE_FILE = CONFIG_DIR / "profiles.toml"


def _default_document() -> dict[str, Any]:
    return {
        "active_profile": "default",
        "profiles": {
            "default": {
                "secret_name": "LITELLM",
                "timeout": 900,
                "react_max_iters": 10,
                "rlm_max_iterations": 30,
                "rlm_max_llm_calls": 50,
                "trace": False,
                "stream": True,
            }
        },
    }


def _ensure_file() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not PROFILE_FILE.exists():
        save_profiles(_default_document())


def load_profiles() -> dict[str, Any]:
    """Load profile TOML as a normalized dictionary."""
    import tomlkit

    _ensure_file()
    parsed = tomlkit.parse(PROFILE_FILE.read_text())
    active_profile = str(parsed.get("active_profile", "default"))
    profiles_raw = parsed.get("profiles", {}) or {}

    profiles: dict[str, dict[str, Any]] = {}
    for name, data in profiles_raw.items():
        if isinstance(data, dict):
            profiles[name] = dict(data)

    if "default" not in profiles:
        profiles["default"] = _default_document()["profiles"]["default"]

    return {"active_profile": active_profile, "profiles": profiles}


def save_profiles(data: dict[str, Any]) -> None:
    """Persist profile dictionary to TOML."""
    import tomlkit

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    doc = tomlkit.document()
    doc["active_profile"] = data.get("active_profile", "default")
    profiles = tomlkit.table()
    for name, profile_data in (data.get("profiles", {}) or {}).items():
        section = tomlkit.table()
        for key, value in profile_data.items():
            section[key] = value
        profiles[name] = section
    doc["profiles"] = profiles
    PROFILE_FILE.write_text(tomlkit.dumps(doc))


def get_profile(name: str | None = None) -> ProfileConfig:
    """Return the requested profile config (or current active profile)."""
    data = load_profiles()
    selected = name or data["active_profile"]
    profile_data = data["profiles"].get(selected)
    if profile_data is None:
        raise ValueError(f"Profile '{selected}' not found")
    return ProfileConfig(name=selected, **profile_data)


def set_active_profile(name: str) -> ProfileConfig:
    """Set and return the active profile."""
    data = load_profiles()
    if name not in data["profiles"]:
        data["profiles"][name] = _default_document()["profiles"]["default"].copy()
    data["active_profile"] = name
    save_profiles(data)
    return get_profile(name)


def update_profile(name: str, updates: dict[str, Any]) -> ProfileConfig:
    """Update profile values and persist."""
    data = load_profiles()
    profile = data["profiles"].setdefault(
        name, _default_document()["profiles"]["default"].copy()
    )
    profile.update({k: v for k, v in updates.items() if v is not None})
    save_profiles(data)
    return get_profile(name)


def resolve_api_key(*, service: str = "fleet-rlm", username: str = "DSPY_LLM_API_KEY") -> str | None:
    """Fetch API key from keyring when available, else return None."""
    try:
        import keyring
    except Exception:
        return None

    try:
        return keyring.get_password(service, username)
    except Exception:
        return None
