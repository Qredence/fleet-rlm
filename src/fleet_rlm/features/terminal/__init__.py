"""Terminal UI components for fleet_rlm chat interface.

This module provides UI and rendering components extracted from terminal_chat.py.
All functions are stateless and take required parameters explicitly.
"""

from .commands import (
    _coerce_value,
    _confirm,
    _confirmation_message,
    _normalize_trace_mode,
    _parse_command_payload,
    _print_unknown_command,
    _safe_split,
    _split_slash_command,
    handle_alias_command,
    handle_reset_command,
    handle_slash_command,
    handle_trace_command,
    print_command_palette,
)
from .settings import (
    _resolve_env_path,
    _write_env_updates,
    check_secret,
    check_secret_key,
    run_long_context,
    run_settings,
    settings_llm,
    settings_modal,
)
from .ui import (
    _FleetCompleter,
    _badge,
    _bottom_toolbar,
    _dialog_style,
    _history_path,
    _iter_mention_paths,
    _mask_secret,
    _print_banner,
    _print_result_inline,
    _prompt_choice,
    _prompt_label,
    _prompt_value,
    _render_shell,
    ROLE_STYLES,
)

__all__ = [
    # commands.py
    "_coerce_value",
    "_confirm",
    "_confirmation_message",
    "_normalize_trace_mode",
    "_parse_command_payload",
    "_print_unknown_command",
    "_safe_split",
    "_split_slash_command",
    "handle_alias_command",
    "handle_reset_command",
    "handle_slash_command",
    "handle_trace_command",
    "print_command_palette",
    # settings.py
    "_resolve_env_path",
    "_write_env_updates",
    "check_secret",
    "check_secret_key",
    "run_long_context",
    "run_settings",
    "settings_llm",
    "settings_modal",
    # ui.py
    "_FleetCompleter",
    "_badge",
    "_bottom_toolbar",
    "_dialog_style",
    "_history_path",
    "_iter_mention_paths",
    "_mask_secret",
    "_print_banner",
    "_print_result_inline",
    "_prompt_choice",
    "_prompt_label",
    "_prompt_value",
    "_render_shell",
    "ROLE_STYLES",
]
