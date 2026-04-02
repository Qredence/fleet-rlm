"""Tests for ``fleet_rlm.runtime.tools.infra_tools``."""

from __future__ import annotations

from fleet_rlm.runtime.tools.infra_tools import (
    build_lsp_tools,
    build_snapshot_tools,
)


class _StubAgent:
    """Minimal stand-in for ``RLMReActChatAgent``."""

    def __init__(self) -> None:
        self.interpreter = None
        self._session = None


def test_build_snapshot_tools_returns_dspy_tools() -> None:
    agent = _StubAgent()
    tools = build_snapshot_tools(agent)  # type: ignore[arg-type]
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}
    assert "list_snapshots" in names
    assert "resolve_snapshot" in names


def test_build_lsp_tools_returns_dspy_tools() -> None:
    agent = _StubAgent()
    tools = build_lsp_tools(agent)  # type: ignore[arg-type]
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}
    assert "lsp_completions" in names
    assert "lsp_document_symbols" in names


def test_snapshot_resolve_returns_graceful_error() -> None:
    """``resolve_snapshot`` tool gracefully returns error when no config."""
    agent = _StubAgent()
    tools = build_snapshot_tools(agent)  # type: ignore[arg-type]
    resolve_tool = next(
        t for t in tools if getattr(t, "name", "") == "resolve_snapshot"
    )
    # Call the wrapped function — it should fail gracefully (no Daytona config)
    result = resolve_tool.func("nonexistent")
    assert isinstance(result, str)
    assert "Error" in result or "not available" in result


def test_lsp_completions_returns_no_session_message() -> None:
    """``lsp_completions`` returns graceful message when no session."""
    agent = _StubAgent()
    tools = build_lsp_tools(agent)  # type: ignore[arg-type]
    comp_tool = next(t for t in tools if getattr(t, "name", "") == "lsp_completions")
    result = comp_tool.func("test.py", 0, 0)
    assert isinstance(result, str)
    assert "No active" in result or "LSP error" in result
