"""Canonical import checks for Wave 7.2 structural cleanup."""

from __future__ import annotations

import importlib


def test_server_canonical_imports() -> None:
    ws_pkg = importlib.import_module("fleet_rlm.server.routers.ws")
    ws_helpers = importlib.import_module("fleet_rlm.server.routers.ws.helpers")
    ws_session = importlib.import_module("fleet_rlm.server.routers.ws.session")
    ws_streaming = importlib.import_module("fleet_rlm.server.routers.ws.streaming")
    execution_events = importlib.import_module("fleet_rlm.server.execution.events")
    execution_step_builder = importlib.import_module(
        "fleet_rlm.server.execution.step_builder"
    )
    execution_sanitizer = importlib.import_module(
        "fleet_rlm.server.execution.sanitizer"
    )
    runtime_settings = importlib.import_module("fleet_rlm.server.runtime_settings")
    legacy_models = importlib.import_module("fleet_rlm.server.legacy_models")

    assert ws_pkg.router is not None
    assert ws_pkg.chat_streaming is not None
    assert ws_pkg.execution_stream is not None
    assert callable(ws_helpers._error_envelope)
    assert callable(ws_session._manifest_path)
    assert callable(ws_streaming._should_reload_docs_path)
    assert execution_events.ExecutionEventEmitter is not None
    assert execution_step_builder.ExecutionStepBuilder is not None
    assert callable(execution_sanitizer.sanitize_event_payload)
    assert callable(runtime_settings.get_settings_snapshot)
    assert legacy_models.Session is not None
    assert legacy_models.Task is not None


def test_react_canonical_imports() -> None:
    signatures = importlib.import_module("fleet_rlm.react.signatures")
    tools_pkg = importlib.import_module("fleet_rlm.react.tools")
    tools_sandbox = importlib.import_module("fleet_rlm.react.tools.sandbox")
    tools_delegate = importlib.import_module("fleet_rlm.react.tools.delegate")
    tools_memory = importlib.import_module("fleet_rlm.react.tools.memory_intelligence")
    tools_filesystem = importlib.import_module("fleet_rlm.react.tools.filesystem")
    tools_document = importlib.import_module("fleet_rlm.react.tools.document")
    tools_chunking = importlib.import_module("fleet_rlm.react.tools.chunking")

    assert signatures.AnalyzeLongDocument is not None
    assert callable(tools_pkg.build_tool_list)
    assert callable(tools_sandbox.build_sandbox_tools)
    assert callable(tools_delegate.build_rlm_delegate_tools)
    assert callable(tools_memory.build_memory_intelligence_tools)
    assert callable(tools_filesystem.build_filesystem_tools)
    assert callable(tools_document.build_document_tools)
    assert callable(tools_chunking.build_chunking_tools)


def test_models_and_terminal_canonical_imports() -> None:
    terminal_chat = importlib.import_module("fleet_rlm.terminal.chat")
    models_pkg = importlib.import_module("fleet_rlm.models")
    models_streaming = importlib.import_module("fleet_rlm.models.streaming")

    assert callable(terminal_chat.run_terminal_chat)
    assert models_pkg.StreamEvent is models_streaming.StreamEvent
    assert models_pkg.TurnState is models_streaming.TurnState
