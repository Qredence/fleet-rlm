"""Guardrails ensuring removed legacy import paths stay deleted."""

from __future__ import annotations

import importlib

import pytest


def _root(path: str) -> str:
    return f"fleet_rlm.{path}"


REMOVED_MODULES = [
    _root("runtime_settings"),
    _root("signatures"),
    _root("terminal_chat"),
    _root("models.models"),
    _root("server.models"),
    _root("server.execution_events"),
    _root("server.execution_step_builder"),
    _root("server.execution_event_sanitizer"),
    _root("server.routers.ws_helpers"),
    _root("server.routers.ws_commands"),
    _root("server.routers.ws_lifecycle"),
    _root("server.routers.ws_message_loop"),
    _root("server.routers.ws_repl_hook"),
    _root("server.routers.ws_session"),
    _root("server.routers.ws_session_store"),
    _root("server.routers.ws_streaming"),
    _root("server.routers.ws_turn"),
    _root("react.tools_sandbox"),
    _root("react.tools_sandbox_helpers"),
    _root("react.tools_rlm_delegate"),
    _root("react.tools_memory_intelligence"),
    _root("react.filesystem_tools"),
    _root("react.document_tools"),
    _root("react.chunking_tools"),
]


@pytest.mark.parametrize("module_name", REMOVED_MODULES)
def test_removed_legacy_import_path_fails(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)
