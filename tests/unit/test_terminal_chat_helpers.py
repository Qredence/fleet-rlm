"""Unit tests for standalone terminal chat helper logic."""

from __future__ import annotations

from pathlib import Path

from prompt_toolkit.document import Document

from fleet_rlm.features.terminal import (
    _FleetCompleter,
    _coerce_value,
    _iter_mention_paths,
    _parse_command_payload,
    _write_env_updates,
)


def test_coerce_value_basic_types() -> None:
    assert _coerce_value("true") is True
    assert _coerce_value("false") is False
    assert _coerce_value("null") is None
    assert _coerce_value("42") == 42
    assert _coerce_value("3.14") == 3.14
    assert _coerce_value("abc") == "abc"


def test_parse_command_payload_from_json() -> None:
    payload = _parse_command_payload('{"path":"README.md","size":20,"append":false}')
    assert payload == {"path": "README.md", "size": 20, "append": False}


def test_parse_command_payload_from_key_values() -> None:
    payload = _parse_command_payload("path=README.md size=20 append=true")
    assert payload == {"path": "README.md", "size": 20, "append": True}


def test_iter_mention_paths_lists_matching_entries(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "README.md").write_text("ok")
    (tmp_path / "src").mkdir()
    monkeypatch.chdir(tmp_path)

    matches = _iter_mention_paths("R")
    assert "README.md" in matches

    dir_matches = _iter_mention_paths("s")
    assert "src/" in dir_matches


def test_fleet_completer_suggests_slash_commands() -> None:
    completer = _FleetCompleter(
        command_specs=[("/settings", "Configure settings"), ("/status", "Show status")],
        command_dispatch_names=[],
    )
    completions = list(
        completer.get_completions(Document(text="/set", cursor_position=4), None)
    )
    texts = {completion.text for completion in completions}
    assert "/settings" in texts


def test_fleet_completer_suggests_mentions(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "src").mkdir()
    monkeypatch.chdir(tmp_path)

    completer = _FleetCompleter(
        command_specs=[],
        command_dispatch_names=[],
    )
    completions = list(
        completer.get_completions(Document(text="@s", cursor_position=2), None)
    )
    texts = {completion.text for completion in completions}
    assert "src/" in texts


def test_write_env_updates_persists_values(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    monkeypatch.delenv("DSPY_LM_MODEL", raising=False)

    _write_env_updates(
        env_path=env_path,
        updates={"DSPY_LM_MODEL": "openai/gpt-4o-mini"},
    )

    content = env_path.read_text()
    assert "DSPY_LM_MODEL=" in content
    assert "openai/gpt-4o-mini" in content
