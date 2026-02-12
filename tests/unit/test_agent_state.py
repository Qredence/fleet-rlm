"""Tests for AgentStateManager state handling."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fleet_rlm.stateful import AgentStateManager


class _FakeInterpreter:
    def __init__(self, run_result):
        self._run_result = run_result

    def execute(self, code: str, variables=None):
        if "Create agent workspace structure" in code:
            return {"status": "ok"}
        return self._run_result


class _FakeSandbox:
    def __init__(self, run_result):
        self.interpreter = _FakeInterpreter(run_result)
        self.storage: dict[str, str] = {}

    def start(self):
        return None

    def shutdown(self):
        return None

    def save_to_workspace(self, path: str, content: str):
        self.storage[path] = content
        return {"status": "ok", "path": path}

    def load_from_workspace(self, path: str):
        if path not in self.storage:
            return {"status": "error", "error": "not found"}
        return {"status": "ok", "content": self.storage[path]}

    def execute_with_rlm(self, _task):
        raise NotImplementedError

    def get_session_stats(self):
        return {}


def _seed_script(
    sandbox: _FakeSandbox, *, name: str = "demo", code: str = "print('ok')"
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    sandbox.storage[f"scripts/{name}.json"] = json.dumps(
        {
            "name": name,
            "code": code,
            "timestamp": now,
            "agent_name": "tester",
            "description": "",
            "version": 1,
            "execution_count": 0,
            "last_result": None,
            "previous_versions": [],
        }
    )


def test_run_script_marks_stderr_string_as_failure(monkeypatch):
    fake_sandbox = _FakeSandbox("[Error] ValueError: boom")
    _seed_script(fake_sandbox, name="demo", code="raise ValueError('boom')")
    monkeypatch.setattr(
        "fleet_rlm.stateful.agent.StatefulSandboxManager",
        lambda **kwargs: fake_sandbox,
    )

    manager = AgentStateManager(agent_name="tester")
    result = manager.run_script("demo")

    assert result.success is False
    assert "ValueError" in (result.error or "")

    saved_meta = json.loads(fake_sandbox.storage["scripts/demo.json"])
    assert saved_meta["execution_count"] == 1
    assert saved_meta["last_result"]["success"] is False
    assert "ValueError" in saved_meta["last_result"]["error"]
