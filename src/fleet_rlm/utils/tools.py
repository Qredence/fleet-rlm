"""Compatibility shim for legacy `fleet_rlm.utils.tools` imports.

Prefer:
    - `fleet_rlm.utils.regex` for regex helpers
    - `fleet_rlm.utils.modal` for Modal helper functions
"""

from __future__ import annotations

# Re-export modal helpers for backward compatibility.
from fleet_rlm.utils.modal import (
    create_interpreter,
    ensure_volume_exists,
    get_default_volume_name,
    get_memory_path,
    get_workspace_volume_name,
    load_modal_config,
    sanitize_key,
    setup_modal_env,
)
from fleet_rlm.utils.regex import regex_extract

__all__ = [
    "create_interpreter",
    "ensure_volume_exists",
    "get_default_volume_name",
    "get_memory_path",
    "get_workspace_volume_name",
    "load_modal_config",
    "regex_extract",
    "sanitize_key",
    "setup_modal_env",
]
