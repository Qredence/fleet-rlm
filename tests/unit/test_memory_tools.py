"""Unit tests for ReAct agent persistent memory tools.

Tests cover memory_read, memory_write, and memory_list tools, ensuring they
generate correct Python code for the sandbox and interact with the interpreter
appropriately (e.g. committing volume changes).
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from dspy.primitives.code_interpreter import FinalOutput
from fleet_rlm.react import RLMReActChatAgent


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeInterpreter:
    def __init__(self):
        self.start_calls = 0
        self.shutdown_calls = 0
        self.commit_calls = 0
        self.execute_calls: list[tuple[str, dict]] = []
        self.default_execution_profile = "RLM_DELEGATE"
        self._volume = True  # Pretend we have a volume

    def start(self):
        self.start_calls += 1

    def shutdown(self):
        self.shutdown_calls += 1

    def commit(self):
        self.commit_calls += 1

    @contextmanager
    def execution_profile(self, profile):
        previous = self.default_execution_profile
        self.default_execution_profile = profile
        try:
            yield self
        finally:
            self.default_execution_profile = previous

    def execute(self, code, variables=None, **kwargs):
        self.execute_calls.append((code, variables or {}))
        # Simulate success for all memory operations
        return FinalOutput(
            {
                "status": "ok",
                "path": (variables or {}).get("path", "unknown"),
                "content": "fake content",
                "items": [{"name": "file1.txt", "type": "file"}],
            }
        )


def _make_fake_react(records):
    class _FakeReAct:
        def __init__(self, *, signature, tools, max_iters):
            records.append(
                {
                    "signature": signature,
                    "tools": tools,
                    "max_iters": max_iters,
                }
            )

        def __call__(self, **kwargs):
            return SimpleNamespace(
                assistant_response="echo",
                trajectory={},
            )

    return _FakeReAct


# ---------------------------------------------------------------------------
# Memory tool tests
# ---------------------------------------------------------------------------


def test_memory_read_generates_read_code(monkeypatch):
    """memory_read should generate python code to read a file."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    fake_interpreter = _FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    # Find the tool
    tool_map = {getattr(t, "name", ""): t for t in agent.react_tools}
    memory_read = tool_map["memory_read"]

    # Execute tool (which is a dspy.Tool wrapper, so we call the function)
    result = memory_read.func(path="/data/test.txt")

    assert result["status"] == "ok"
    assert len(fake_interpreter.execute_calls) == 1
    code, vars = fake_interpreter.execute_calls[0]

    assert 'open(path, "r"' in code
    assert vars["path"] == "/data/test.txt"


def test_memory_list_generates_listdir_code(monkeypatch):
    """memory_list should generate python code to list directory contents."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    fake_interpreter = _FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    tool_map = {getattr(t, "name", ""): t for t in agent.react_tools}
    memory_list = tool_map["memory_list"]

    result = memory_list.func(path="/data/docs")

    assert result["status"] == "ok"
    assert len(fake_interpreter.execute_calls) == 1
    code, vars = fake_interpreter.execute_calls[0]

    assert "os.listdir(path)" in code
    assert vars["path"] == "/data/docs"


def test_memory_write_generates_write_code_and_commits(monkeypatch):
    """memory_write should generate write code, sync, and trigger interpreter commit."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    fake_interpreter = _FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    tool_map = {getattr(t, "name", ""): t for t in agent.react_tools}
    memory_write = tool_map["memory_write"]

    result = memory_write.func(path="/data/new.txt", content="hello world")

    assert result["status"] == "ok"
    # Check Code Execution
    assert len(fake_interpreter.execute_calls) == 1
    code, vars = fake_interpreter.execute_calls[0]

    assert 'open(path, "w"' in code
    assert "os.sync()" in code
    assert vars["path"] == "/data/new.txt"
    assert vars["content"] == "hello world"

    # Check Host-Side Commit
    assert fake_interpreter.commit_calls == 1


def test_memory_write_skips_commit_if_no_volume(monkeypatch):
    """memory_write should not crash or commit if agent has no volume."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    fake_interpreter = _FakeInterpreter()
    fake_interpreter._volume = None  # No volume configured
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    tool_map = {getattr(t, "name", ""): t for t in agent.react_tools}
    memory_write = tool_map["memory_write"]

    result = memory_write.func(path="/data/new.txt", content="hello")

    assert result["status"] == "ok"
    assert fake_interpreter.commit_calls == 0


def test_core_memory_append_within_limit(monkeypatch):
    """core_memory_append should update host-side state if within limits."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    fake_interpreter = _FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    # Initialize basic state
    agent._core_memory = {"scratchpad": "Task: Init"}
    agent._core_memory_limits = {"scratchpad": 100}

    result = agent.core_memory_append("scratchpad", "Update: Step 1")

    assert "Appended to 'scratchpad'" in result
    assert "Task: Init\nUpdate: Step 1" in agent._core_memory["scratchpad"]


def test_core_memory_append_exceeds_limit(monkeypatch):
    """core_memory_append should error if new content exceeds limit."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    fake_interpreter = _FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    agent._core_memory = {"scratchpad": "A" * 90}
    agent._core_memory_limits = {"scratchpad": 100}

    # Try to append 20 chars (90 + 20 + 1 = 111 > 100)
    result = agent.core_memory_append("scratchpad", "B" * 20)

    assert "Error: Appending content would exceed limit" in result
    # State should remain unchanged
    assert len(agent._core_memory["scratchpad"]) == 90


def test_core_memory_replace_success(monkeypatch):
    """core_memory_replace should overwrite block content."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    fake_interpreter = _FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    agent._core_memory = {"scratchpad": "Old Code"}
    agent._core_memory_limits = {"scratchpad": 100}

    result = agent.core_memory_replace("scratchpad", "New Code")

    assert "Updated block 'scratchpad'" in result
    assert agent._core_memory["scratchpad"] == "New Code"
