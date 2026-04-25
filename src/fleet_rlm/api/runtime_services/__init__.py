"""Service helpers for runtime settings, diagnostics, and volume routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .chat_runtime import (
        ChatSessionState,
        PreparedChatRuntime,
        build_chat_agent_context,
        new_chat_session_state,
        prepare_chat_runtime,
        set_interpreter_default_profile,
    )
    from .chat_persistence import (
        ExecutionLifecycleManager,
        build_local_persist_fn,
        ensure_manifest_shape,
        initialize_turn_lifecycle,
        persist_memory_item_if_needed,
        persist_session_state,
        sync_session_record_state,
        update_manifest_from_exported_state,
    )
    from .diagnostics import (
        build_runtime_status_response,
        run_daytona_connection_test,
        run_lm_connection_test,
    )
    from .settings import (
        apply_runtime_settings_patch,
        build_runtime_settings_snapshot,
    )
    from .volumes import (
        load_volume_file_content,
        load_volume_list,
        load_volume_tree,
        resolve_daytona_volume_name,
    )

__all__ = [
    "ChatSessionState",
    "PreparedChatRuntime",
    "apply_runtime_settings_patch",
    "build_chat_agent_context",
    "build_local_persist_fn",
    "build_runtime_settings_snapshot",
    "build_runtime_status_response",
    "ensure_manifest_shape",
    "ExecutionLifecycleManager",
    "initialize_turn_lifecycle",
    "load_volume_file_content",
    "load_volume_list",
    "load_volume_tree",
    "new_chat_session_state",
    "persist_memory_item_if_needed",
    "persist_session_state",
    "prepare_chat_runtime",
    "resolve_daytona_volume_name",
    "run_daytona_connection_test",
    "run_lm_connection_test",
    "set_interpreter_default_profile",
    "sync_session_record_state",
    "update_manifest_from_exported_state",
]

_IMPORT_MAP: dict[str, tuple[str, str]] = {
    "ChatSessionState": ("fleet_rlm.api.runtime_services.chat_runtime", "ChatSessionState"),
    "PreparedChatRuntime": ("fleet_rlm.api.runtime_services.chat_runtime", "PreparedChatRuntime"),
    "build_chat_agent_context": ("fleet_rlm.api.runtime_services.chat_runtime", "build_chat_agent_context"),
    "new_chat_session_state": ("fleet_rlm.api.runtime_services.chat_runtime", "new_chat_session_state"),
    "prepare_chat_runtime": ("fleet_rlm.api.runtime_services.chat_runtime", "prepare_chat_runtime"),
    "set_interpreter_default_profile": ("fleet_rlm.api.runtime_services.chat_runtime", "set_interpreter_default_profile"),
    "ExecutionLifecycleManager": ("fleet_rlm.api.runtime_services.chat_persistence", "ExecutionLifecycleManager"),
    "build_local_persist_fn": ("fleet_rlm.api.runtime_services.chat_persistence", "build_local_persist_fn"),
    "ensure_manifest_shape": ("fleet_rlm.api.runtime_services.chat_persistence", "ensure_manifest_shape"),
    "initialize_turn_lifecycle": ("fleet_rlm.api.runtime_services.chat_persistence", "initialize_turn_lifecycle"),
    "persist_memory_item_if_needed": ("fleet_rlm.api.runtime_services.chat_persistence", "persist_memory_item_if_needed"),
    "persist_session_state": ("fleet_rlm.api.runtime_services.chat_persistence", "persist_session_state"),
    "sync_session_record_state": ("fleet_rlm.api.runtime_services.chat_persistence", "sync_session_record_state"),
    "update_manifest_from_exported_state": ("fleet_rlm.api.runtime_services.chat_persistence", "update_manifest_from_exported_state"),
    "build_runtime_status_response": ("fleet_rlm.api.runtime_services.diagnostics", "build_runtime_status_response"),
    "run_daytona_connection_test": ("fleet_rlm.api.runtime_services.diagnostics", "run_daytona_connection_test"),
    "run_lm_connection_test": ("fleet_rlm.api.runtime_services.diagnostics", "run_lm_connection_test"),
    "apply_runtime_settings_patch": ("fleet_rlm.api.runtime_services.settings", "apply_runtime_settings_patch"),
    "build_runtime_settings_snapshot": ("fleet_rlm.api.runtime_services.settings", "build_runtime_settings_snapshot"),
    "load_volume_file_content": ("fleet_rlm.api.runtime_services.volumes", "load_volume_file_content"),
    "load_volume_list": ("fleet_rlm.api.runtime_services.volumes", "load_volume_list"),
    "load_volume_tree": ("fleet_rlm.api.runtime_services.volumes", "load_volume_tree"),
    "resolve_daytona_volume_name": ("fleet_rlm.api.runtime_services.volumes", "resolve_daytona_volume_name"),
}


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module_name, attr_name = _IMPORT_MAP[name]
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
