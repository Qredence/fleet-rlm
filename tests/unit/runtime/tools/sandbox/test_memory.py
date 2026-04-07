"""Unit tests for ReAct agent persistent memory tools.

Tests cover memory_read, memory_write, and memory_list tools, ensuring they
generate correct Python code for the sandbox and interact with the interpreter
appropriately (e.g. committing volume changes).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.runtime.agent import RLMReActChatAgent
from fleet_rlm.runtime.tools.sandbox import common as sandbox_common
from fleet_rlm.runtime.tools.sandbox import storage as sandbox_storage_tools
from tests.unit.fixtures_daytona import FakeDaytonaStorageSession
from tests.unit.fixtures_react import FakeInterpreter

pytestmark = pytest.mark.usefixtures("react_records")


# ---------------------------------------------------------------------------
# Memory tool tests
# ---------------------------------------------------------------------------


def test_memory_read_generates_read_code(monkeypatch):
    """memory_read should generate python code to read a file."""
    fake_interpreter = FakeInterpreter()
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
    assert fake_interpreter.reload_calls == 1


def test_memory_read_uses_daytona_session(monkeypatch):
    """Daytona-backed memory_read should use native session file APIs."""
    fake_interpreter = FakeInterpreter()
    fake_interpreter.volume_mount_path = "/home/daytona/memory"
    agent = RLMReActChatAgent(interpreter=fake_interpreter)
    session = FakeDaytonaStorageSession()
    session.file_contents["/home/daytona/memory/memory/notes/native.txt"] = (
        "hello from daytona"
    )

    async def _fake_get_daytona_session(ctx):
        _ = ctx
        return session

    monkeypatch.setattr(
        sandbox_storage_tools, "_aget_daytona_session", _fake_get_daytona_session
    )

    tool_map = {getattr(t, "name", ""): t for t in agent.react_tools}
    memory_read = tool_map["memory_read"]

    result = memory_read.func(path="notes/native.txt")

    assert result == {
        "status": "ok",
        "path": "/home/daytona/memory/memory/notes/native.txt",
        "content": "hello from daytona",
        "chars": len("hello from daytona"),
    }
    assert session.read_calls == ["/home/daytona/memory/memory/notes/native.txt"]
    assert fake_interpreter.execute_calls == []
    assert fake_interpreter.reload_calls == 0


def test_memory_list_generates_listdir_code(monkeypatch):
    """memory_list should generate python code to list directory contents."""
    fake_interpreter = FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    tool_map = {getattr(t, "name", ""): t for t in agent.react_tools}
    memory_list = tool_map["memory_list"]

    result = memory_list.func(path="/data/docs")

    assert result["status"] == "ok"
    assert len(fake_interpreter.execute_calls) == 1
    code, vars = fake_interpreter.execute_calls[0]

    assert "os.listdir(path)" in code
    assert vars["path"] == "/data/docs"
    assert fake_interpreter.reload_calls == 1


def test_memory_list_uses_daytona_session(monkeypatch):
    """Daytona-backed memory_list should use native fs listings."""
    fake_interpreter = FakeInterpreter()
    fake_interpreter.volume_mount_path = "/home/daytona/memory"
    agent = RLMReActChatAgent(interpreter=fake_interpreter)
    session = FakeDaytonaStorageSession()
    session.list_entries["/home/daytona/memory/memory/docs"] = [
        SimpleNamespace(name="guides", is_dir=True),
        SimpleNamespace(name="readme.txt", is_dir=False),
    ]

    async def _fake_get_daytona_session(ctx):
        _ = ctx
        return session

    monkeypatch.setattr(
        sandbox_storage_tools, "_aget_daytona_session", _fake_get_daytona_session
    )

    tool_map = {getattr(t, "name", ""): t for t in agent.react_tools}
    memory_list = tool_map["memory_list"]

    result = memory_list.func(path="docs")

    assert result == {
        "status": "ok",
        "path": "/home/daytona/memory/memory/docs",
        "items": [
            {"name": "guides", "type": "dir"},
            {"name": "readme.txt", "type": "file"},
        ],
        "count": 2,
    }
    assert session.list_calls == ["/home/daytona/memory/memory/docs"]
    assert fake_interpreter.execute_calls == []
    assert fake_interpreter.reload_calls == 0


def test_memory_write_generates_write_code_and_commits(monkeypatch):
    """memory_write should generate write code, sync, and trigger interpreter commit."""
    fake_interpreter = FakeInterpreter()
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


def test_memory_write_rejects_invalid_python_syntax(monkeypatch):
    """memory_write should refuse to persist invalid Python content."""
    fake_interpreter = FakeInterpreter(
        execute_result_factory=lambda code, payload: (
            {
                "status": "error",
                "error": "invalid syntax (line 1)",
            }
            if 'compile(content, path, "exec")' in code
            else {
                "status": "ok",
                "path": payload.get("path", "unknown"),
                "chars": len(payload.get("content", "")),
            }
        )
    )
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    tool_map = {getattr(t, "name", ""): t for t in agent.react_tools}
    memory_write = tool_map["memory_write"]

    result = memory_write.func(path="scripts/bad.py", content="def broken(:\n    pass")

    assert result["status"] == "error"
    assert "Python syntax validation failed" in result["error"]
    assert len(fake_interpreter.execute_calls) == 1
    assert fake_interpreter.commit_calls == 0


def test_memory_write_uses_daytona_session(monkeypatch):
    """Daytona-backed memory_write should use native session file APIs."""
    fake_interpreter = FakeInterpreter()
    fake_interpreter.volume_mount_path = "/home/daytona/memory"
    agent = RLMReActChatAgent(interpreter=fake_interpreter)
    session = FakeDaytonaStorageSession()

    async def _fake_get_daytona_session(ctx):
        _ = ctx
        return session

    monkeypatch.setattr(
        sandbox_storage_tools, "_aget_daytona_session", _fake_get_daytona_session
    )

    tool_map = {getattr(t, "name", ""): t for t in agent.react_tools}
    memory_write = tool_map["memory_write"]

    result = memory_write.func(path="notes/daytona.txt", content="native write")

    assert result == {
        "status": "ok",
        "path": "/home/daytona/memory/memory/notes/daytona.txt",
        "chars": len("native write"),
    }
    assert fake_interpreter.execute_calls == []
    assert session.write_calls == [
        ("/home/daytona/memory/memory/notes/daytona.txt", "native write")
    ]
    assert fake_interpreter.commit_calls == 0


def test_memory_write_skips_commit_if_no_volume(monkeypatch):
    """memory_write should not crash or commit if agent has no volume."""
    fake_interpreter = FakeInterpreter(has_volume=False)
    fake_interpreter._volume = None  # No volume configured
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    tool_map = {getattr(t, "name", ""): t for t in agent.react_tools}
    memory_write = tool_map["memory_write"]

    result = memory_write.func(path="/data/new.txt", content="hello")

    assert result["status"] == "ok"
    assert fake_interpreter.commit_calls == 0


def test_memory_write_resolves_relative_path_under_data_memory(monkeypatch):
    """Relative paths should resolve to /data/memory for safer defaults."""
    fake_interpreter = FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    tool_map = {getattr(t, "name", ""): t for t in agent.react_tools}
    memory_write = tool_map["memory_write"]

    result = memory_write.func(path="notes/new.txt", content="hello")

    assert result["status"] == "ok"
    assert len(fake_interpreter.execute_calls) == 1
    _code, vars = fake_interpreter.execute_calls[0]
    assert vars["path"] == "/data/memory/notes/new.txt"


def test_memory_write_rejects_path_escape(monkeypatch):
    """Path traversal outside /data should be rejected before sandbox execution."""
    fake_interpreter = FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    tool_map = {getattr(t, "name", ""): t for t in agent.react_tools}
    memory_write = tool_map["memory_write"]

    result = memory_write.func(path="../../etc/passwd", content="nope")

    assert result["status"] == "error"
    assert "Path must stay within mounted volume root" in result["error"]
    assert fake_interpreter.execute_calls == []


def test_write_to_file_append_mode(monkeypatch):
    """write_to_file should support append mode for persistent notes/logs."""
    fake_interpreter = FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    tool_map = {getattr(t, "name", ""): t for t in agent.react_tools}
    write_to_file = tool_map["write_to_file"]

    result = write_to_file.func(path="logs/session.txt", content="line1\n", append=True)

    assert result["status"] == "ok"
    assert len(fake_interpreter.execute_calls) == 1
    code, vars = fake_interpreter.execute_calls[0]
    assert 'open(path, "a", encoding="utf-8")' in code
    assert vars["path"] == "/data/memory/logs/session.txt"


def test_write_to_file_append_uses_daytona_session(monkeypatch):
    """Daytona-backed write_to_file append should use native session file APIs."""
    fake_interpreter = FakeInterpreter()
    fake_interpreter.volume_mount_path = "/home/daytona/memory"
    agent = RLMReActChatAgent(interpreter=fake_interpreter)
    session = FakeDaytonaStorageSession()
    session.file_contents["/home/daytona/memory/memory/logs/session.txt"] = "line0\n"

    async def _fake_get_daytona_session(ctx):
        _ = ctx
        return session

    monkeypatch.setattr(
        sandbox_storage_tools, "_aget_daytona_session", _fake_get_daytona_session
    )

    tool_map = {getattr(t, "name", ""): t for t in agent.react_tools}
    write_to_file = tool_map["write_to_file"]

    result = write_to_file.func(path="logs/session.txt", content="line1\n", append=True)

    assert result == {
        "status": "ok",
        "path": "/home/daytona/memory/memory/logs/session.txt",
        "chars": len("line1\n"),
        "mode": "append",
    }
    assert session.read_calls == ["/home/daytona/memory/memory/logs/session.txt"]
    assert session.write_calls == [
        ("/home/daytona/memory/memory/logs/session.txt", "line0\nline1\n")
    ]
    assert fake_interpreter.execute_calls == []
    assert fake_interpreter.commit_calls == 0


def test_load_text_from_volume_uses_daytona_session(monkeypatch):
    """Daytona-backed load_text_from_volume should hydrate document cache natively."""
    fake_interpreter = FakeInterpreter()
    fake_interpreter.volume_mount_path = "/home/daytona/memory"
    agent = RLMReActChatAgent(interpreter=fake_interpreter)
    session = FakeDaytonaStorageSession()
    session.file_contents["/home/daytona/memory/artifacts/docs/spec.md"] = (
        "# Spec\nHello"
    )

    async def _fake_get_daytona_session(ctx):
        _ = ctx
        return session

    monkeypatch.setattr(
        sandbox_common, "_aget_daytona_session", _fake_get_daytona_session
    )

    tool_map = {getattr(t, "name", ""): t for t in agent.react_tools}
    load_text_from_volume = tool_map["load_text_from_volume"]

    result = load_text_from_volume.func(path="docs/spec.md", alias="spec")

    assert result == {
        "status": "ok",
        "alias": "spec",
        "path": "/home/daytona/memory/artifacts/docs/spec.md",
        "chars": len("# Spec\nHello"),
        "lines": 2,
    }
    assert session.read_calls == ["/home/daytona/memory/artifacts/docs/spec.md"]
    assert agent.documents["spec"] == "# Spec\nHello"
    assert agent.active_alias == "spec"
    assert fake_interpreter.execute_calls == []


def test_edit_core_memory_tool_append(monkeypatch):
    """edit_core_memory tool should route append edits through host core memory API."""
    fake_interpreter = FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)
    agent._core_memory = {
        "persona": "p",
        "human": "h",
        "scratchpad": "Task start",
    }
    agent._core_memory_limits = {"persona": 2000, "human": 2000, "scratchpad": 1000}

    tool_map = {getattr(t, "name", ""): t for t in agent.react_tools}
    edit_core_memory = tool_map["edit_core_memory"]

    result = edit_core_memory.func(
        section="scratchpad",
        content="step 1",
        mode="append",
    )

    assert result["status"] == "ok"
    assert result["section"] == "scratchpad"
    assert "step 1" in agent._core_memory["scratchpad"]


def test_core_memory_append_within_limit(monkeypatch):
    """core_memory_append should update host-side state if within limits."""
    fake_interpreter = FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    # Initialize basic state
    agent._core_memory = {"scratchpad": "Task: Init"}
    agent._core_memory_limits = {"scratchpad": 100}

    result = agent.core_memory_append("scratchpad", "Update: Step 1")

    assert "Appended to 'scratchpad'" in result
    assert "Task: Init\nUpdate: Step 1" in agent._core_memory["scratchpad"]


def test_core_memory_append_exceeds_limit(monkeypatch):
    """core_memory_append should error if new content exceeds limit."""
    fake_interpreter = FakeInterpreter()
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
    fake_interpreter = FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    agent._core_memory = {"scratchpad": "Old Code"}
    agent._core_memory_limits = {"scratchpad": 100}

    result = agent.core_memory_replace("scratchpad", "New Code")

    assert "Updated block 'scratchpad'" in result
    assert agent._core_memory["scratchpad"] == "New Code"


def test_set_core_memory_copies_external_mapping(monkeypatch):
    """set_core_memory should snapshot external mappings instead of aliasing them."""
    fake_interpreter = FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    external_memory = {"scratchpad": "First draft"}
    agent.set_core_memory(external_memory)
    external_memory["scratchpad"] = "Mutated later"

    assert agent._core_memory["scratchpad"] == "First draft"
