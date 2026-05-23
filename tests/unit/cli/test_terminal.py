from __future__ import annotations

import sys
from types import ModuleType

import pytest


class _FakeConsole:
    def __init__(self) -> None:
        self.printed: list[object] = []
        self.cleared = False

    def print(self, message: object) -> None:
        self.printed.append(message)

    def clear(self) -> None:
        self.cleared = True


class _FakePreferences:
    def __init__(self) -> None:
        self.theme = "dark"


class _FakeSession:
    def __init__(self) -> None:
        self.console = _FakeConsole()
        self.trace_mode = "compact"
        self.command_permissions: dict[str, str] = {}
        self._preferences = _FakePreferences()
        self.saved_preferences = 0
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.results: list[tuple[str, object]] = []
        self.settings_calls: list[str] = []

    def _save_preferences(self) -> None:
        self.saved_preferences += 1

    def _print_error(self, message: str) -> None:
        self.errors.append(message)

    def _print_warning(self, message: str) -> None:
        self.warnings.append(message)

    def _print_result(self, result: object, *, title: str) -> None:
        self.results.append((title, result))

    def _run_settings(self, section: str) -> None:
        self.settings_calls.append(section)


class _FakeAgent:
    def __init__(self) -> None:
        self.reset_calls: list[bool] = []

    def reset(self, *, clear_sandbox_buffers: bool) -> dict[str, str]:
        self.reset_calls.append(clear_sandbox_buffers)
        return {"status": "reset"}


def test_split_slash_command_normalizes_input() -> None:
    from fleet_rlm.cli.terminal.commands import _split_slash_command

    command, arg_text = _split_slash_command("  /TRACE   verbose now  ")

    assert command == "/trace"
    assert arg_text == "verbose now"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"count": 2, "enabled": true}', {"count": 2, "enabled": True}),
        ("path=README.md retries=3 enabled=false", {"path": "README.md", "retries": 3, "enabled": False}),
    ],
)
def test_parse_command_payload_supports_json_and_key_values(raw: str, expected: dict[str, object]) -> None:
    from fleet_rlm.cli.terminal.commands import _parse_command_payload

    assert _parse_command_payload(raw) == expected


def test_handle_slash_command_dispatches_help(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.cli.terminal import commands

    session = _FakeSession()
    agent = _FakeAgent()
    help_calls: list[tuple[object, object, str]] = []
    fake_chat = ModuleType("fleet_rlm.cli.terminal.chat")
    fake_chat._COMMAND_SPECS = ("fake-spec",)
    monkeypatch.setitem(sys.modules, "fleet_rlm.cli.terminal.chat", fake_chat)
    monkeypatch.setattr(
        commands,
        "_show_help",
        lambda current_session, command_specs, arg: help_calls.append((current_session, command_specs, arg)),
    )

    should_exit = commands.handle_slash_command(session, agent, "/help reset")

    assert should_exit is False
    assert help_calls == [(session, ("fake-spec",), "reset")]


def test_handle_slash_command_resets_agent_after_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.cli.terminal import commands

    session = _FakeSession()
    agent = _FakeAgent()
    monkeypatch.setattr(commands, "_confirm", lambda question: True)

    should_exit = commands.handle_slash_command(session, agent, "/reset")

    assert should_exit is False
    assert agent.reset_calls == [True]
    assert session.results == [("reset", {"status": "reset"})]


def test_handle_slash_command_updates_trace_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.cli.terminal import commands

    session = _FakeSession()
    agent = _FakeAgent()

    should_exit = commands.handle_slash_command(session, agent, "/trace verbose")

    assert should_exit is False
    assert session.trace_mode == "verbose"
    assert session.saved_preferences == 1
    assert session.console.printed == ["[green]Trace mode set to verbose[/]"]


def test_handle_slash_command_dispatches_alias_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.cli.terminal import commands

    session = _FakeSession()
    agent = _FakeAgent()
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        commands,
        "_execute_agent_command",
        lambda current_session, current_agent, command, payload: calls.append((command, payload)),
    )

    should_exit = commands.handle_slash_command(session, agent, "/buffer scratch")

    assert should_exit is False
    assert calls == [("read_buffer", {"name": "scratch"})]
