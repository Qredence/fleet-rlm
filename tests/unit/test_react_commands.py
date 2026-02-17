from __future__ import annotations

import pytest

from fleet_rlm.react.commands import execute_command


class _FakeAgent:
    def __init__(self):
        self.calls = []

        def analyze_long_document(**kwargs):
            self.calls.append(("analyze_long_document", kwargs))
            return {"status": "ok"}

        def write_to_file(**kwargs):
            self.calls.append(("write_to_file", kwargs))
            return {"status": "ok"}

        def edit_core_memory(**kwargs):
            self.calls.append(("edit_core_memory", kwargs))
            return {"status": "ok"}

        self.react_tools = [analyze_long_document, write_to_file, edit_core_memory]


@pytest.mark.asyncio
async def test_execute_command_passes_include_trajectory_to_analyze_document():
    agent = _FakeAgent()
    result = await execute_command(
        agent,
        "analyze_document",
        {"query": "q", "include_trajectory": False},
    )

    assert result["status"] == "ok"
    assert agent.calls
    _, kwargs = agent.calls[0]
    assert kwargs["query"] == "q"
    assert kwargs["include_trajectory"] is False


@pytest.mark.asyncio
async def test_execute_command_dispatches_write_to_file():
    agent = _FakeAgent()
    result = await execute_command(
        agent,
        "write_to_file",
        {"path": "notes/day1.txt", "content": "hello", "append": True},
    )

    assert result["status"] == "ok"
    tool_name, kwargs = agent.calls[0]
    assert tool_name == "write_to_file"
    assert kwargs["path"] == "notes/day1.txt"
    assert kwargs["content"] == "hello"
    assert kwargs["append"] is True


@pytest.mark.asyncio
async def test_execute_command_dispatches_edit_core_memory():
    agent = _FakeAgent()
    result = await execute_command(
        agent,
        "edit_core_memory",
        {"section": "scratchpad", "content": "next", "mode": "replace"},
    )

    assert result["status"] == "ok"
    tool_name, kwargs = agent.calls[0]
    assert tool_name == "edit_core_memory"
    assert kwargs["section"] == "scratchpad"
    assert kwargs["mode"] == "replace"
