import pytest
from unittest.mock import MagicMock
from dspy import Tool

from src.fleet_rlm.react.tools import build_tool_list
from src.fleet_rlm.react.agent import RLMReActChatAgent


@pytest.fixture
def mock_agent():
    agent = MagicMock(spec=RLMReActChatAgent)
    agent.interpreter = MagicMock()
    return agent


def test_memory_tool_registration(mock_agent):
    """Verify search_evolutive_memory is correctly registered in the DSPy tool list."""
    tools = build_tool_list(mock_agent)

    # Check that tools were returned
    assert len(tools) > 0, "Expected tools to be registered"

    # Search for the evolutive memory tool by name
    memory_tool_found = False
    for tool in tools:
        if (
            isinstance(tool, Tool)
            and getattr(tool.func, "__name__", "") == "search_evolutive_memory"
        ):
            memory_tool_found = True
            break
        elif (
            getattr(tool, "__name__", getattr(tool, "name", ""))
            == "search_evolutive_memory"
        ):
            memory_tool_found = True
            break

    assert memory_tool_found, (
        "search_evolutive_memory tool was not found in the tool list"
    )
