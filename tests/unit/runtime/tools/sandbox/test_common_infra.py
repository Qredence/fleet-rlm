"""Tests for sandbox infra helpers in ``fleet_rlm.runtime.tools.sandbox.common``."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fleet_rlm.runtime.tools.sandbox.common import (
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


def test_snapshot_list_serializes_success_path(monkeypatch) -> None:
    async def _fake_list_snapshots() -> list[dict[str, object]]:
        return [
            {"name": "fleet-rlm-base", "state": "ACTIVE", "image_name": "python:3.12"}
        ]

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.runtime.alist_snapshots",
        _fake_list_snapshots,
    )
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.async_compat._run_async_compat",
        lambda fn, *args: asyncio.run(fn(*args)),
    )

    agent = _StubAgent()
    tools = build_snapshot_tools(agent)  # type: ignore[arg-type]
    list_tool = next(t for t in tools if getattr(t, "name", "") == "list_snapshots")
    result = json.loads(list_tool.func(10))
    assert result == [
        {"name": "fleet-rlm-base", "state": "ACTIVE", "image_name": "python:3.12"}
    ]


def test_lsp_tools_serialize_success_paths(monkeypatch) -> None:
    class _FakeLspServer:
        def __init__(self) -> None:
            self.started = False
            self.opened_path: str | None = None
            self.stopped = False

        async def start(self) -> None:
            self.started = True

        async def did_open(self, file_path: str) -> None:
            self.opened_path = file_path

        async def completions(self, file_path: str, line: int, character: int):
            _ = (file_path, line, character)
            return [SimpleNamespace(label="helper", kind="Function")]

        async def document_symbols(self, file_path: str):
            _ = file_path
            return [SimpleNamespace(name="Widget", kind="Class")]

        async def stop(self) -> None:
            self.stopped = True

    class _FakeSession:
        def __init__(self) -> None:
            self.server = _FakeLspServer()
            self.languages: list[str] = []

        def create_lsp_server(self, *, language: str):
            self.languages.append(language)
            return self.server

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.async_compat._run_async_compat",
        lambda fn, *args: asyncio.run(fn(*args)),
    )

    agent = _StubAgent()
    session = _FakeSession()
    agent._session = session
    tools = build_lsp_tools(agent)  # type: ignore[arg-type]

    completions_tool = next(
        t for t in tools if getattr(t, "name", "") == "lsp_completions"
    )
    symbols_tool = next(
        t for t in tools if getattr(t, "name", "") == "lsp_document_symbols"
    )

    completions = json.loads(completions_tool.func("app.py", 3, 5))
    symbols = json.loads(symbols_tool.func("app.py"))

    assert session.languages == ["python", "python"]
    assert session.server.started is True
    assert session.server.opened_path == "app.py"
    assert session.server.stopped is True
    assert completions == ["helper (Function)"]
    assert symbols == ["Widget (Class)"]
