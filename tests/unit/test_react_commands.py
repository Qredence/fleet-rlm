from __future__ import annotations

import pytest

from fleet_rlm.react.commands import execute_command


class _FakeAgent:
    def __init__(self):
        self.calls = []

        def analyze_long_document(**kwargs):
            self.calls.append(("analyze_long_document", kwargs))
            return {"status": "ok"}

        self.react_tools = [analyze_long_document]


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
