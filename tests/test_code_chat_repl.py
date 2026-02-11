from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from dspy.primitives.code_interpreter import FinalOutput

from fleet_rlm.interactive.models import SessionConfig
from fleet_rlm.interactive.session import CodeChatSession


class _FakeUI:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def banner(self, *, profile_name: str, trace: bool, stream: bool) -> None:
        self.events.append(("banner", {"profile_name": profile_name, "trace": trace, "stream": stream}))

    def info(self, message: str) -> None:
        self.events.append(("info", message))

    def error(self, message: str) -> None:
        self.events.append(("error", message))

    def assistant(self, message: str) -> None:
        self.events.append(("assistant", message))

    def trace(self, payload):
        self.events.append(("trace", payload))

    def data(self, title: str, payload):
        self.events.append(("data", {"title": title, "payload": payload}))

    def show_help(self) -> None:
        self.events.append(("help", None))


class _QueuePrompt:
    def __init__(self, inputs: list[str]) -> None:
        self.inputs = list(inputs)

    def prompt(self, _label: str) -> str:
        if not self.inputs:
            raise EOFError
        return self.inputs.pop(0)


class _FakeInterpreter:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, code: str):
        self.executed.append(code)
        return FinalOutput({"ok": True, "code": code})


class _FakeAgent:
    def __init__(self) -> None:
        self.history = SimpleNamespace(messages=[])
        self.react_tools = [self.load_document]
        self.interpreter = _FakeInterpreter()
        self.loaded: list[tuple[str, str]] = []

    def reset(self, *, clear_sandbox_buffers: bool = True):
        self.history.messages = []
        return {"status": "ok", "buffers_cleared": clear_sandbox_buffers}

    def load_document(self, path: str, alias: str = "active"):
        self.loaded.append((path, alias))
        return {"status": "ok", "path": path, "alias": alias}

    def list_documents(self):
        return {"documents": [{"alias": "active", "chars": 10, "lines": 2}], "active_alias": "active"}

    def save_buffer_to_volume(self, name: str, path: str):
        return {"status": "ok", "name": name, "path": path}

    def load_text_from_volume(self, path: str, alias: str = "active"):
        return {"status": "ok", "path": path, "alias": alias}

    def chat_turn(self, message: str):
        self.history.messages.append({"user_request": message, "assistant_response": "stub"})
        return {"assistant_response": "stub", "trajectory": {}}

    def chat_turn_stream(self, *, message: str, trace: bool = False):
        return self.chat_turn(message)


def _build_session(monkeypatch, tmp_path: Path) -> tuple[CodeChatSession, _FakeAgent, _FakeUI]:
    monkeypatch.setattr("fleet_rlm.interactive.legacy_session.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "fleet_rlm.interactive.legacy_session.CodeChatSession._configure_logging",
        lambda self: None,
    )
    monkeypatch.setattr(
        "fleet_rlm.interactive.legacy_session.PromptSession",
        lambda *args, **kwargs: _QueuePrompt([]),
    )

    ui = _FakeUI()
    agent = _FakeAgent()
    session = CodeChatSession(
        agent=agent,
        config=SessionConfig(profile_name="test", trace=False, stream=False),
        ui=ui,
    )
    return session, agent, ui


def test_command_routing_and_parser(monkeypatch, tmp_path: Path):
    session, agent, ui = _build_session(monkeypatch, tmp_path)

    assert session._handle_command('/load "docs/my file.txt"') is False
    assert agent.loaded == [("docs/my file.txt", "active")]

    assert session._handle_command("/trace on") is False
    assert session.trace is True

    assert session._handle_command("/tools") is False
    assert session._handle_command("/exit") is True

    assert any(evt[0] == "data" and evt[1]["title"] == "load" for evt in ui.events)
    assert any(evt[0] == "data" and evt[1]["title"] == "tools" for evt in ui.events)


def test_py_multiline_executes_in_modal_interpreter(monkeypatch, tmp_path: Path):
    session, agent, ui = _build_session(monkeypatch, tmp_path)
    session.prompt = _QueuePrompt(["x = 1", "SUBMIT(x)", ":end"])

    assert session._handle_command("/py") is False
    assert agent.interpreter.executed == ["x = 1\nSUBMIT(x)"]
    assert any(evt[0] == "data" and evt[1]["title"] == "python-result" for evt in ui.events)


def test_run_loop_handles_slash_commands(monkeypatch, tmp_path: Path):
    session, _agent, ui = _build_session(monkeypatch, tmp_path)
    session.prompt = _QueuePrompt(["/help", "/exit"])

    session.run()

    assert ui.events[0][0] == "banner"
    assert any(evt[0] == "help" for evt in ui.events)
    assert any(evt[0] == "info" and "Exiting code-chat." in str(evt[1]) for evt in ui.events)
