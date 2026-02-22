import pytest
from unittest.mock import MagicMock, patch

from fleet_rlm.react.tools import build_tool_list
from fleet_rlm.react.agent import RLMReActChatAgent


@pytest.fixture
def mock_agent():
    agent = MagicMock(spec=RLMReActChatAgent)
    agent.interpreter = MagicMock()
    return agent


def test_memory_tool_registration(mock_agent):
    """Verify search_evolutive_memory is correctly registered in the DSPy tool list.

    The memory tool import may fail in CI when database dependencies
    (pgvector, asyncpg, etc.) are not fully available.  We patch the
    import site so the tool is always loadable during this unit test.
    """

    def _dummy_search(query: str) -> str:  # noqa: ARG001
        return "mock"

    with patch.dict(
        "sys.modules",
        {
            "fleet_rlm.core.memory_tools": MagicMock(
                search_evolutive_memory=_dummy_search
            ),
        },
    ):
        tools = build_tool_list(mock_agent)

    # Check that tools were returned
    assert len(tools) > 0, "Expected tools to be registered"

    # Search for the evolutive memory tool by name
    memory_tool_found = False
    for tool in tools:
        name = getattr(tool, "name", None) or getattr(
            getattr(tool, "func", None), "__name__", ""
        )
        if name == "search_evolutive_memory" or name == "_dummy_search":
            memory_tool_found = True
            break

    assert memory_tool_found, (
        "search_evolutive_memory tool was not found in the tool list"
    )
