"""Re-export stub: lifecycle logic lives in api/runtime_services/chat_persistence."""

from fleet_rlm.api.runtime_services.chat_persistence import (
    EXECUTION_TO_RUN_STEP_TYPE,
    PersistenceRequiredError,
    build_execution_event,
    build_startup_status_event,
    build_workspace_task_request,
    cancel_startup_status_task,
    cancel_task,
    cancelled_event_payload,
    classify_stream_failure,
    emit_delayed_startup_status,
    enqueue_latest_nonblocking,
    get_execution_emitter,
    get_execution_emitter_with_config,
    handle_chat_disconnect,
    map_execution_step_type,
    should_reload_docs_path,
)

from .transport import chat_startup_error_payload, handle_chat_loop_exception

__all__ = [
    "PersistenceRequiredError",
    "classify_stream_failure",
    "chat_startup_error_payload",
    "EXECUTION_TO_RUN_STEP_TYPE",
    "build_execution_event",
    "get_execution_emitter",
    "get_execution_emitter_with_config",
    "map_execution_step_type",
    "build_startup_status_event",
    "emit_delayed_startup_status",
    "cancel_startup_status_task",
    "should_reload_docs_path",
    "enqueue_latest_nonblocking",
    "cancelled_event_payload",
    "cancel_task",
    "handle_chat_disconnect",
    "handle_chat_loop_exception",
    "build_workspace_task_request",
]
