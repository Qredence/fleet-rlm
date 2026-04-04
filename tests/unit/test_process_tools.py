"""Tests for ``fleet_rlm.runtime.tools.process_tools`` provider-gating."""

from __future__ import annotations

from fleet_rlm.runtime.tools.sandbox_common import (
    _UNSUPPORTED_PROVIDER_ERROR,
    build_process_tools,
)
from fleet_rlm.runtime.tools.sandbox_common import build_sandbox_tools


class _StubInterpreter:
    """Non-Daytona interpreter stub."""

    _volume = None


class _StubAgent:
    """Minimal stand-in for ``RLMReActChatAgent`` with a non-Daytona interpreter."""

    def __init__(self) -> None:
        self.interpreter = _StubInterpreter()
        self._session = None


_UNIVERSAL_TOOL_NAMES = ("workspace_write", "workspace_read")
_DAYTONA_ONLY_TOOL_NAMES = (
    "run",
    "extract_python_ast",
    "start_background_process",
    "read_process_logs",
    "kill_process",
)


def _tool_names(tools: list) -> set[str]:
    return {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}


def _find_tool(tools: list, name: str):
    tool = next((t for t in tools if getattr(t, "name", "") == name), None)
    assert tool is not None, f"expected tool '{name}' to be present in tool list"
    return tool


def test_universal_tools_always_present() -> None:
    """workspace_write and workspace_read are registered for all providers."""
    agent = _StubAgent()
    tools = build_process_tools(agent)  # type: ignore[arg-type]
    names = _tool_names(tools)
    for name in _UNIVERSAL_TOOL_NAMES:
        assert name in names


def test_daytona_only_stubs_present_for_non_daytona() -> None:
    """Daytona-only tool names are still registered but as stubs returning an error."""
    agent = _StubAgent()
    tools = build_process_tools(agent)  # type: ignore[arg-type]
    names = _tool_names(tools)
    for name in _DAYTONA_ONLY_TOOL_NAMES:
        assert name in names, f"expected stub tool '{name}' to be present"


def test_daytona_only_stubs_return_stable_error_payload() -> None:
    """Stub tools return the stable _UNSUPPORTED_PROVIDER_ERROR dict instead of raising NameError."""
    agent = _StubAgent()
    tools = build_process_tools(agent)  # type: ignore[arg-type]

    run_tool = _find_tool(tools, "run")
    ast_tool = _find_tool(tools, "extract_python_ast")
    bg_tool = _find_tool(tools, "start_background_process")
    logs_tool = _find_tool(tools, "read_process_logs")
    kill_tool = _find_tool(tools, "kill_process")

    for result in (
        run_tool.func("echo hi"),
        ast_tool.func("main.py"),
        bg_tool.func("srv", "python -m http.server"),
        logs_tool.func("srv"),
        kill_tool.func("srv"),
    ):
        assert result == _UNSUPPORTED_PROVIDER_ERROR
        assert result.get("status") == "error"
        assert isinstance(result.get("error"), str)


def test_build_sandbox_tools_skips_daytona_infra_tools_for_non_daytona(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "fleet_rlm.runtime.tools.sandbox_delegate_tools.build_rlm_delegate_tools",
        lambda agent: ["delegate"],
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.tools.sandbox_memory_tools.build_memory_intelligence_tools",
        lambda agent: ["memory"],
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.tools.sandbox_storage_tools.build_storage_tools",
        lambda agent: ["storage"],
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.tools.sandbox_common.build_snapshot_tools",
        lambda agent: ["snapshot"],
    )
    monkeypatch.setattr(
        "fleet_rlm.runtime.tools.sandbox_common.build_lsp_tools",
        lambda agent: ["lsp"],
    )

    agent = _StubAgent()

    assert build_sandbox_tools(agent) == ["delegate", "memory", "storage"]
