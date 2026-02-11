from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from fleet_rlm.cli import app
from fleet_rlm.interactive import DependencyCheck


runner = CliRunner()


def test_cli_help_lists_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run-basic" in result.stdout
    assert "run-architecture" in result.stdout
    assert "run-react-chat" in result.stdout
    assert "check-secret" in result.stdout


def test_cli_bad_docs_path_returns_error():
    result = runner.invoke(
        app,
        [
            "run-architecture",
            "--docs-path",
            "missing-docs.txt",
            "--query",
            "extract modules",
        ],
    )
    assert result.exit_code == 1
    assert (
        "Docs path does not exist" in result.stdout
        or "Docs path does not exist" in result.stderr
    )


def test_cli_requires_docs_path_for_architecture():
    result = runner.invoke(
        app,
        [
            "run-architecture",
            "--query",
            "extract modules",
        ],
    )
    assert result.exit_code == 2
    assert "docs-path" in result.stdout or "docs-path" in result.stderr


def test_init_list_shows_all_categories():
    result = runner.invoke(app, ["init", "--list"])
    assert result.exit_code == 0
    assert "Available Skills:" in result.stdout
    assert "Available Agents:" in result.stdout
    assert "Available Teams:" in result.stdout
    assert "Available Hooks:" in result.stdout


def test_init_default_installs_all_categories(tmp_path: Path):
    target = tmp_path / "claude"
    result = runner.invoke(app, ["init", "--target", str(target)])
    assert result.exit_code == 0
    assert (target / "skills").exists()
    assert (target / "agents").exists()
    assert (target / "teams").exists()
    assert (target / "hooks").exists()


def test_init_teams_only(tmp_path: Path):
    target = tmp_path / "claude"
    result = runner.invoke(app, ["init", "--target", str(target), "--teams-only"])
    assert result.exit_code == 0
    assert (target / "teams").exists()
    assert not (target / "skills").exists()
    assert not (target / "agents").exists()
    assert not (target / "hooks").exists()


def test_init_hooks_only(tmp_path: Path):
    target = tmp_path / "claude"
    result = runner.invoke(app, ["init", "--target", str(target), "--hooks-only"])
    assert result.exit_code == 0
    assert (target / "hooks").exists()
    assert not (target / "skills").exists()
    assert not (target / "agents").exists()
    assert not (target / "teams").exists()


def test_init_no_teams_no_hooks(tmp_path: Path):
    target = tmp_path / "claude"
    result = runner.invoke(
        app, ["init", "--target", str(target), "--no-teams", "--no-hooks"]
    )
    assert result.exit_code == 0
    assert (target / "skills").exists()
    assert (target / "agents").exists()
    assert not (target / "teams").exists()
    assert not (target / "hooks").exists()


def test_init_rejects_multiple_only_modes(tmp_path: Path):
    target = tmp_path / "claude"
    result = runner.invoke(
        app, ["init", "--target", str(target), "--skills-only", "--agents-only"]
    )
    assert result.exit_code == 1
    assert (
        "Only one --*-only mode" in result.stdout
        or "Only one --*-only mode" in result.stderr
    )


def test_init_rejects_only_mode_with_exclusion(tmp_path: Path):
    target = tmp_path / "claude"
    result = runner.invoke(
        app, ["init", "--target", str(target), "--teams-only", "--no-hooks"]
    )
    assert result.exit_code == 1
    assert (
        "--*-only modes cannot be combined" in result.stdout
        or "--*-only modes cannot be combined" in result.stderr
    )


def test_run_react_chat_help():
    result = runner.invoke(app, ["run-react-chat", "--help"])
    assert result.exit_code == 0
    assert "--profile" in result.stdout
    assert "--react-max-iters" in result.stdout
    assert "--rlm-max-iterations" in result.stdout
    assert "--rlm-max-llm-calls" in result.stdout
    assert "--legacy" in result.stdout
    assert "--trace-mode" in result.stdout
    assert "--stream-refresh-ms" in result.stdout


def test_code_chat_help():
    result = runner.invoke(app, ["code-chat", "--help"])
    assert result.exit_code == 0
    assert "--react-max-iters" in result.stdout
    assert "--trace" in result.stdout
    assert "--no-stream" in result.stdout
    assert "--legacy" in result.stdout
    assert "--trace-mode" in result.stdout
    assert "--stream-refresh-ms" in result.stdout


def test_run_react_chat_exit(monkeypatch):
    class _FakeChatAgent:
        def __init__(self):
            self.history = SimpleNamespace(messages=[])
            self.react_tools = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def reset(self, *, clear_sandbox_buffers=True):
            self.history = SimpleNamespace(messages=[])
            return {"status": "ok", "buffers_cleared": clear_sandbox_buffers}

        def load_document(self, path, alias="active"):
            return {"status": "ok", "path": path, "alias": alias}

        def chat_turn(self, message):
            self.history.messages.append(
                {"user_request": message, "assistant_response": "stub"}
            )
            return {"assistant_response": "stub", "trajectory": {}}

    monkeypatch.setattr(
        "fleet_rlm.runners.build_react_chat_agent",
        lambda **kwargs: _FakeChatAgent(),
    )

    result = runner.invoke(app, ["run-react-chat", "--legacy"], input="/exit\n")
    assert result.exit_code == 0
    assert "fleet-rlm code-chat" in result.stdout
    assert "Exiting code-chat." in result.stdout


def test_run_react_chat_aliases_to_code_chat(monkeypatch):
    calls = []

    def _fake_run_code_chat_session(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("fleet_rlm.cli._run_code_chat_session", _fake_run_code_chat_session)

    result = runner.invoke(app, ["run-react-chat", "--react-max-iters", "3"])
    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["react_max_iters"] == 3
    assert calls[0]["legacy"] is False


def test_code_chat_legacy_routes_to_prompt_toolkit(monkeypatch):
    calls = {"legacy": 0}

    class _FakeChatAgent:
        history = SimpleNamespace(messages=[])
        react_tools = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

    class _FakeLegacySession:
        def __init__(self, *, agent, config):
            self.agent = agent
            self.config = config

        def run(self):
            calls["legacy"] += 1

    monkeypatch.setattr(
        "fleet_rlm.interactive.check_interactive_dependencies",
        lambda **kwargs: DependencyCheck(ok=True, missing=[]),
    )
    monkeypatch.setattr(
        "fleet_rlm.runners.build_react_chat_agent",
        lambda **kwargs: _FakeChatAgent(),
    )
    monkeypatch.setattr(
        "fleet_rlm.interactive.legacy_session.CodeChatSession",
        _FakeLegacySession,
    )

    result = runner.invoke(app, ["code-chat", "--legacy"])
    assert result.exit_code == 0
    assert calls["legacy"] == 1


def test_code_chat_missing_extras_exits_with_install_hint(monkeypatch):
    monkeypatch.setattr(
        "fleet_rlm.interactive.check_interactive_dependencies",
        lambda **kwargs: DependencyCheck(ok=False, missing=["textual", "rich"]),
    )

    result = runner.invoke(app, ["code-chat"])
    assert result.exit_code == 2
    out = result.stdout + result.stderr
    assert "dependencies are missing" in out
    assert "uv sync --extra dev --extra interactive" in out
