from __future__ import annotations

import builtins
import io
import json
import sys

from fleet_rlm.core.driver import sandbox_driver


def _run_driver(monkeypatch, lines: list[str]) -> list[dict]:
    iterator = iter(lines)

    def fake_input() -> str:
        try:
            return next(iterator)
        except StopIteration as exc:
            raise EOFError from exc

    proto_out = io.StringIO()
    monkeypatch.setattr(builtins, "input", fake_input)
    monkeypatch.setattr(sys, "__stdout__", proto_out)

    sandbox_driver()

    raw_lines = [line for line in proto_out.getvalue().splitlines() if line.strip()]
    return [json.loads(line) for line in raw_lines]


def test_submit_positional_mapping(monkeypatch):
    command = {
        "code": "SUBMIT(7, 'ok')",
        "variables": {},
        "tool_names": [],
        "output_names": ["count", "status"],
    }
    messages = _run_driver(monkeypatch, [json.dumps(command)])

    assert len(messages) == 1
    assert messages[0]["final"] == {"count": 7, "status": "ok"}


def test_tool_call_roundtrip(monkeypatch):
    command = {
        "code": "total = add(2, 3)\nSUBMIT(total)",
        "variables": {},
        "tool_names": ["add"],
        "output_names": ["sum"],
    }
    tool_reply = {"tool_result": 5}

    messages = _run_driver(monkeypatch, [json.dumps(command), json.dumps(tool_reply)])

    assert len(messages) == 2
    assert messages[0]["tool_call"]["name"] == "add"
    assert messages[1]["final"] == {"sum": 5}


def test_final_variable_does_not_mask_runtime_error(monkeypatch):
    command = {
        "code": "Final = {'ok': True}\nraise RuntimeError('boom')",
        "variables": {},
        "tool_names": [],
        "output_names": [],
    }
    messages = _run_driver(monkeypatch, [json.dumps(command)])

    assert len(messages) == 1
    assert messages[0]["final"] is None
    assert "RuntimeError: boom" in messages[0]["stderr"]


def test_final_variable_does_not_leak_across_commands(monkeypatch):
    command_one = {
        "code": "Final = {'stale': True}\nSUBMIT('ok')",
        "variables": {},
        "tool_names": [],
        "output_names": ["status"],
    }
    command_two = {
        "code": "x = 1",
        "variables": {},
        "tool_names": [],
        "output_names": [],
    }
    messages = _run_driver(
        monkeypatch, [json.dumps(command_one), json.dumps(command_two)]
    )

    assert len(messages) == 2
    assert messages[0]["final"] == {"status": "ok"}
    assert messages[1]["final"] is None
