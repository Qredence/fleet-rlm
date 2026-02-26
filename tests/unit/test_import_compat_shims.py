"""Import compatibility checks for Wave 7.1 structural shims."""

from __future__ import annotations

import importlib


def test_server_legacy_model_shim_imports() -> None:
    legacy = importlib.import_module("fleet_rlm.server.legacy_models")
    shim = importlib.import_module("fleet_rlm.server.models")

    assert shim.Session is legacy.Session
    assert shim.Task is legacy.Task


def test_ws_flat_shims_resolve_to_packaged_modules() -> None:
    ws_pkg = importlib.import_module("fleet_rlm.server.routers.ws")
    assert ws_pkg.router is not None
    assert ws_pkg.chat_streaming is not None
    assert ws_pkg.execution_stream is not None

    ws_helpers = importlib.import_module("fleet_rlm.server.routers.ws_helpers")
    ws_session = importlib.import_module("fleet_rlm.server.routers.ws_session")
    ws_streaming = importlib.import_module("fleet_rlm.server.routers.ws_streaming")

    assert callable(ws_helpers._error_envelope)
    assert callable(ws_session._manifest_path)
    assert callable(ws_streaming._should_reload_docs_path)


def test_root_targeted_cleanup_shims_import() -> None:
    runtime_settings = importlib.import_module("fleet_rlm.runtime_settings")
    terminal_chat = importlib.import_module("fleet_rlm.terminal_chat")
    signatures = importlib.import_module("fleet_rlm.signatures")

    assert callable(runtime_settings.get_settings_snapshot)
    assert callable(terminal_chat.run_terminal_chat)
    assert signatures.AnalyzeLongDocument is not None


def test_react_tool_shims_import() -> None:
    react_tools_pkg = importlib.import_module("fleet_rlm.react.tools")
    sandbox_shim = importlib.import_module("fleet_rlm.react.tools_sandbox")
    delegate_shim = importlib.import_module("fleet_rlm.react.tools_rlm_delegate")
    memory_shim = importlib.import_module("fleet_rlm.react.tools_memory_intelligence")
    fs_shim = importlib.import_module("fleet_rlm.react.filesystem_tools")
    doc_shim = importlib.import_module("fleet_rlm.react.document_tools")
    chunk_shim = importlib.import_module("fleet_rlm.react.chunking_tools")

    assert callable(react_tools_pkg.build_tool_list)
    assert callable(sandbox_shim.build_sandbox_tools)
    assert callable(delegate_shim.build_rlm_delegate_tools)
    assert callable(memory_shim.build_memory_intelligence_tools)
    assert callable(fs_shim.build_filesystem_tools)
    assert callable(doc_shim.build_document_tools)
    assert callable(chunk_shim.build_chunking_tools)


def test_execution_facade_shims_import() -> None:
    events = importlib.import_module("fleet_rlm.server.execution_events")
    step_builder = importlib.import_module("fleet_rlm.server.execution_step_builder")
    sanitizer = importlib.import_module("fleet_rlm.server.execution_event_sanitizer")

    assert events.ExecutionEventEmitter is not None
    assert step_builder.ExecutionStepBuilder is not None
    assert callable(sanitizer.sanitize_event_payload)
