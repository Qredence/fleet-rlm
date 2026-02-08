"""Utility helpers for RLM operations.

This module provides common utilities for working with Modal sandboxes
and RLM execution, including volume management and default configurations.
"""

from __future__ import annotations

import getpass
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fleet_rlm import ModalInterpreter


def get_default_volume_name() -> str:
    """Generate a consistent volume name for the current user/workspace.

    The volume name is derived from the current username and workspace
    directory name to ensure persistence across sessions for the same
    user/workspace combination.

    Returns:
        Volume name in the format: rlm-data-{user}-{workspace}

    Example:
        >>> get_default_volume_name()
        'rlm-data-alice-myproject'
    """
    user = getpass.getuser()
    workspace = Path.cwd().name
    # Clean up names to be valid Modal volume names (alphanumeric, hyphens)
    user_clean = "".join(c if c.isalnum() else "-" for c in user).lower()
    workspace_clean = "".join(c if c.isalnum() else "-" for c in workspace).lower()
    return f"rlm-data-{user_clean}-{workspace_clean}"


def get_workspace_volume_name(workspace_id: str | None = None) -> str:
    """Get a volume name for a specific workspace.

    Args:
        workspace_id: Optional workspace identifier. If not provided,
            uses the current directory name.

    Returns:
        Volume name for the workspace.
    """
    user = getpass.getuser()
    user_clean = "".join(c if c.isalnum() else "-" for c in user).lower()

    if workspace_id:
        workspace_clean = "".join(
            c if c.isalnum() else "-" for c in workspace_id
        ).lower()
    else:
        workspace_clean = "".join(
            c if c.isalnum() else "-" for c in Path.cwd().name
        ).lower()

    return f"rlm-data-{user_clean}-{workspace_clean}"


def ensure_volume_exists(volume_name: str | None = None) -> str:
    """Ensure a Modal volume exists, creating it if necessary.

    Args:
        volume_name: Name of the volume. If None, uses the default
            volume name for the current user/workspace.

    Returns:
        The volume name (created or existing).
    """
    import modal

    name = volume_name or get_default_volume_name()

    # Ensure volume exists using from_name (V2 compatible)
    modal.Volume.from_name(name, create_if_missing=True)
    return name


def load_modal_config() -> dict[str, str]:
    """Load Modal configuration from ~/.modal.toml.

    Parses the TOML config file to find the active profile and extract
    credentials. This handles the profile-based config structure where
    credentials are stored under profile sections.

    Returns:
        Dictionary with token_id and token_secret if found, empty otherwise.

    Example:
        >>> config = load_modal_config()
        >>> print(config.get("token_id"))
        'ak-...'
    """
    config_path = Path.home() / ".modal.toml"

    if not config_path.exists():
        return {}

    try:
        import tomllib  # type: ignore

        with open(config_path, "rb") as f:
            config = tomllib.load(f)

        # Find active profile
        active_profile = None
        for profile_name, profile_data in config.items():
            if isinstance(profile_data, dict) and profile_data.get("active"):
                active_profile = profile_data
                break

        # Fallback to first profile with credentials
        if not active_profile:
            for profile_name, profile_data in config.items():
                if isinstance(profile_data, dict) and "token_id" in profile_data:
                    active_profile = profile_data
                    break

        if active_profile:
            return {
                "token_id": active_profile.get("token_id", ""),
                "token_secret": active_profile.get("token_secret", ""),
            }

    except Exception:
        pass

    return {}


def setup_modal_env() -> bool:
    """Set up Modal environment variables from config file.

    Loads credentials from ~/.modal.toml and sets them as environment
    variables if not already set.

    Returns:
        True if credentials were loaded, False otherwise.
    """
    # Only load if not already in environment
    if os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"):
        return True

    config = load_modal_config()

    if config.get("token_id") and config.get("token_secret"):
        os.environ["MODAL_TOKEN_ID"] = config["token_id"]
        os.environ["MODAL_TOKEN_SECRET"] = config["token_secret"]
        return True

    return False


def create_interpreter(
    timeout: int = 600,
    volume_name: str | None = None,
    auto_volume: bool = True,
) -> "ModalInterpreter":
    """Create a ModalInterpreter with sensible defaults.

    This helper creates an interpreter with automatic volume management.
    If auto_volume is True and no volume_name is provided, it will
    use or create a default volume for the current user/workspace.

    Args:
        timeout: Sandbox timeout in seconds. Default: 600 (10 minutes)
        volume_name: Specific volume to use. If None and auto_volume
            is True, uses the default volume name.
        auto_volume: Whether to automatically use/create a volume.

    Returns:
        Configured ModalInterpreter instance.

    Example:
        >>> interpreter = create_interpreter(timeout=300)
        >>> interpreter.start()
        >>> result = interpreter.execute("print('Hello')")
        >>> interpreter.shutdown()
    """
    from fleet_rlm import ModalInterpreter

    # Ensure Modal env is set up
    setup_modal_env()

    # Determine volume name
    vol_name = None
    if auto_volume:
        vol_name = volume_name or get_default_volume_name()

    return ModalInterpreter(
        timeout=timeout,
        volume_name=vol_name,
    )


def get_memory_path(key: str) -> Path:
    """Get the filesystem path for a memory key.

    Args:
        key: Memory key identifier.

    Returns:
        Path object for the memory file.
    """
    return Path("/data/memory") / f"{key}.json"


def sanitize_key(key: str) -> str:
    """Sanitize a memory key for filesystem safety.

    Args:
        key: Raw key string.

    Returns:
        Sanitized key safe for use as filename.
    """
    # Replace problematic characters with underscores
    safe_chars = []
    for c in key:
        if c.isalnum() or c in "-_.":
            safe_chars.append(c)
        else:
            safe_chars.append("_")
    return "".join(safe_chars) or "unnamed"
