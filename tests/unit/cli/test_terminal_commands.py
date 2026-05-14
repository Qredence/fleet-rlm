from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.cli.terminal import commands


class _FakeConsole:
    def __init__(self, events: list[tuple[str, object]]):
        self._events = events

    def print(self, message: str) -> None:
        self._events.append(("console.print", message))

    def clear(self) -> None:
        self._events.append(("console.clear", None))


class _FakePreferences:
    def __init__(self) -> None:
        self.theme = "dark"
        self.trace_mode = "compact"
        self.command_permissions: dict[str, str] = {}


class _FakeSession:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []
        self.console = _FakeConsole(self.events)
        self.trace_mode = "compact"
        self.command_permissions: dict[str, str] = {}
        self._preferences = _FakePreferences()
        self._last_user_message: str | None = None
        self._last_command: tuple[str, str] | None = None
        self._undo_stack: list[tuple[str, dict]] = []

    def _print_banner(self, *, planner_ready: bool) -> None:
        self.events.append(("banner", planner_ready))

    def _print_status(self, agent: object) -> None:
        self.events.append(("status", agent))

    def _run_settings(self, value: str) -> None:
        self.events.append(("settings", value))

    def _print_permissions(self) -> None:
        self.events.append(("permissions", None))

    def _print_warning(self, message: str) -> None:
        self.events.append(("warning", message))

    def _print_error(self, message: str) -> None:
        self.events.append(("error", message))

    def _run_long_context(self, value: str) -> None:
        self.events.append(("long_context", value))

    def _append_transcript(self, kind: str, message: str) -> None:
        self.events.append(("transcript", kind))
        self.events.append(("transcript_message", message))

    def _render_shell(self) -> None:
        self.events.append(("render", None))

    def _print_result(self, result: object, *, title: str) -> None:
        self.events.append(("result", title))

    def _save_preferences(self) -> None:
        self.events.append(("save_preferences", None))

    async def _run_chat_turn(self, agent, message):
        self.events.append(("chat_turn", message))


class _FakeAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def reset(self, *, clear_sandbox_buffers: bool) -> dict[str, str]:
        self.calls.append(("reset", clear_sandbox_buffers))
        return {"status": "ok"}


def test_handle_slash_command_routes_session_registry_commands(monkeypatch):
    session = _FakeSession()
    agent = _FakeAgent()
    palette_calls: list[tuple[object, object]] = []
    monkeypatch.setattr(
        commands,
        "print_command_palette",
        lambda session, agent: palette_calls.append((session, agent)) or False,
    )
    monkeypatch.setattr(commands, "_confirm", lambda question: True)

    assert commands.handle_slash_command(session, agent, "/") is False
    assert commands.handle_slash_command(session, agent, "/help") is False
    assert commands.handle_slash_command(session, agent, "/commands") is False
    assert commands.handle_slash_command(session, agent, "/?") is False
    assert commands.handle_slash_command(session, agent, "/exit") is True
    assert commands.handle_slash_command(session, agent, "/clear") is False
    assert commands.handle_slash_command(session, agent, "/reset") is False
    assert commands.handle_slash_command(session, agent, "/trace verbose") is False
    assert commands.handle_slash_command(session, agent, "/status") is False
    assert commands.handle_slash_command(session, agent, "/settings model") is False
    assert commands.handle_slash_command(session, agent, "/model") is False
    assert commands.handle_slash_command(session, agent, "/permissions") is False
    assert commands.handle_slash_command(session, agent, "/permissions-reset") is False
    assert commands.handle_slash_command(session, agent, "/run-long-context docs") is False
    # /help now uses _show_help instead of the palette; only / and /commands trigger it
    assert len(palette_calls) == 2
    # /? now prints a Rich Table via console.print instead of appending to transcript
    from rich.table import Table

    # /help renders a Rich Table (command reference) via console.print
    help_tables = [val for key, val in session.events if key == "console.print" and isinstance(val, Table)]
    assert len(help_tables) >= 2  # at least one from /help and one from /?
    assert ("console.print", "[dim]bye[/dim]") in session.events
    assert ("console.clear", None) in session.events
    assert ("banner", True) in session.events
    assert ("result", "reset") in session.events
    assert agent.calls == [("reset", True)]
    assert session.trace_mode == "verbose"
    assert ("console.print", "[green]Trace mode set to verbose[/]") in session.events
    assert ("status", agent) in session.events
    assert ("settings", "model") in session.events
    assert ("permissions", None) in session.events
    assert ("warning", "Permission policy reset.") in session.events
    assert ("long_context", "docs") in session.events


def test_handle_slash_command_clear_buffer_prompts_before_clearing_all(monkeypatch):
    session = _FakeSession()
    agent = _FakeAgent()
    calls: list[tuple[str, dict[str, object]]] = []
    confirmations: list[str] = []
    warnings: list[str] = []
    monkeypatch.setattr(
        commands,
        "_execute_agent_command",
        lambda session, agent, command, args: calls.append((command, args)),
    )
    monkeypatch.setattr(
        commands,
        "_confirm",
        lambda question: confirmations.append(question) or True,
    )
    monkeypatch.setattr(
        session,
        "_print_warning",
        lambda message: warnings.append(message),
    )

    assert commands.handle_slash_command(session, agent, "/clear-buffer") is False
    assert confirmations == ["Clear all buffers?"]
    assert warnings == []
    assert calls == [("clear_buffer", {})]


@pytest.mark.parametrize(
    ("line", "expected_tool", "expected_payload"),
    [
        (
            "/docs README.md alias",
            "load_document",
            {"path": "README.md", "alias": "alias"},
        ),
        (
            "/load README.md alias",
            "load_document",
            {"path": "README.md", "alias": "alias"},
        ),
        ("/active docs", "set_active_document", {"alias": "docs"}),
        ("/list", "list_documents", {}),
        ("/chunk headers 200", "chunk_host", {"strategy": "headers", "size": 200}),
        ("/summarize Key points", "summarize_long_document", {"focus": "Key points"}),
        ("/extract Error logs", "extract_from_logs", {"query": "Error logs"}),
        (
            "/semantic Find auth flows",
            "parallel_semantic_map",
            {"query": "Find auth flows"},
        ),
        ("/buffer scratch", "read_buffer", {"name": "scratch"}),
        ("/clear-buffer scratch", "clear_buffer", {"name": "scratch"}),
        (
            "/save-buffer scratch /tmp/work.txt",
            "save_buffer_to_volume",
            {"name": "scratch", "path": "/tmp/work.txt"},
        ),
        (
            "/load-volume /tmp/report.md alias",
            "load_text_from_volume",
            {"path": "/tmp/report.md", "alias": "alias"},
        ),
    ],
)
def test_handle_slash_command_routes_alias_registry_commands(
    monkeypatch,
    line: str,
    expected_tool: str,
    expected_payload: dict[str, object],
):
    session = _FakeSession()
    agent = _FakeAgent()
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        commands,
        "_execute_agent_command",
        lambda session, agent, command, args: calls.append((command, args)),
    )
    monkeypatch.setattr(commands, "_confirm", lambda question: True)

    assert commands.handle_slash_command(session, agent, line) is False
    assert calls == [(expected_tool, expected_payload)]


def test_handle_slash_command_reports_unknown_command():
    session = _FakeSession()
    agent = SimpleNamespace()

    assert commands.handle_slash_command(session, agent, "/does-not-exist") is False
    assert session.events[-1] == (
        "error",
        "Unknown command: /does-not-exist. Type /help for commands.",
    )


def test_theme_command_sets_dark():
    session = _FakeSession()
    agent = _FakeAgent()

    assert commands.handle_slash_command(session, agent, "/theme dark") is False
    assert session._preferences.theme == "dark"
    assert ("save_preferences", None) in session.events
    assert ("console.print", "[green]Theme set to dark[/]") in session.events


def test_theme_command_sets_light():
    session = _FakeSession()
    agent = _FakeAgent()

    assert commands.handle_slash_command(session, agent, "/theme light") is False
    assert session._preferences.theme == "light"
    assert ("save_preferences", None) in session.events
    assert ("console.print", "[green]Theme set to light[/]") in session.events


def test_theme_command_auto_detects(monkeypatch):
    session = _FakeSession()
    agent = _FakeAgent()

    monkeypatch.setenv("COLORFGBG", "0;15")
    assert commands.handle_slash_command(session, agent, "/theme auto") is False
    assert session._preferences.theme == "light"
    assert ("save_preferences", None) in session.events


def test_theme_command_invalid_shows_error():
    session = _FakeSession()
    agent = _FakeAgent()

    assert commands.handle_slash_command(session, agent, "/theme neon") is False
    assert ("error", "usage: /theme <dark|light|auto>") in session.events
