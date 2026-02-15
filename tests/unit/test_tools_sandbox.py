"""Unit tests for sandbox tools (edit_file)."""

from typing import Any
import pytest
from unittest.mock import MagicMock
from fleet_rlm.react.agent import RLMReActChatAgent
from fleet_rlm.react.tools import build_tool_list
import dspy


class _FakeInterpreter:
    """Mock interpreter that captures execution."""

    def __init__(self):
        self.last_code = ""
        self.last_vars = {}

    def execute(
        self, code: str, variables: dict[str, Any], execution_profile: Any = None
    ):
        self.last_code = code
        self.last_vars = variables
        # Return a mock result that indicates success for edit_file
        # In reality, the sandbox code runs and returns SUBMIT(...)
        # We simulate the wrapper parsing the result.
        # But wait, execute_submit wraps the result.
        # The tools.py implementaiton calls execute_submit which calls agent.interpreter.execute.
        # If the result is a FinalOutput, it extracts it.
        # Let's return a MagicMock that mimics FinalOutput(output={...})

        # We need to return valid output structure for what the tool expects
        # edit_file returns whatever execute_submit returns

        return dspy.primitives.code_interpreter.FinalOutput(
            output={
                "status": "ok",
                "path": variables.get("path"),
                "message": "File updated successfully",
            }
        )

    def execution_profile(self, profile):
        return MagicMock()


@pytest.fixture
def mock_agent():
    agent = MagicMock(spec=RLMReActChatAgent)
    agent.interpreter = _FakeInterpreter()
    # Mock documents property
    agent.documents = {}
    # Mock depth tracking
    agent._max_depth = 2
    agent._current_depth = 0
    return agent


def test_edit_file_generates_correct_code(mock_agent):
    """Test that edit_file generates the expected python code for the sandbox."""
    tools = build_tool_list(mock_agent)
    edit_tool = next(t for t in tools if t.name == "edit_file")

    # Run the tool
    path = "/tmp/test.py"
    old = "def foo(): pass"
    new = "def foo(): return 1"

    edit_tool(path=path, old_snippet=old, new_snippet=new)

    # Check that it called execute with correct code structure
    interpreter = mock_agent.interpreter
    code = interpreter.last_code
    vars = interpreter.last_vars

    # Verify variables
    assert vars["path"] == path
    assert vars["old_snippet"] == old
    assert vars["new_snippet"] == new

    # Verify code logic contains the key ambiguity checks
    assert "count = content.count(old_snippet)" in code
    assert "if count == 0:" in code
    assert "elif count > 1:" in code
    assert "content.replace(old_snippet, new_snippet)" in code
    assert 'with open(path, "w", encoding="utf-8") as f:' in code


def test_rlm_query_spawns_sub_agent(mock_agent):
    """Test that rlm_query spawns a sub-agent."""
    # We need to mock the RLMReActChatAgent class effectively since rlm_query instantiates it.
    # rlm_query uses `agent.__class__`.

    # Mock the __class__ of our mock_agent to return a Mock class
    MockAgentClass = MagicMock()
    mock_instance = MockAgentClass.return_value
    # DSPy 3.1.3 uses 'assistant_response' key
    mock_instance.chat_turn.return_value = {"assistant_response": "42"}
    mock_instance.history.messages = ["a", "b"]

    mock_agent.__class__ = MockAgentClass

    tools = build_tool_list(mock_agent)
    query_tool = next(t for t in tools if t.name == "rlm_query")

    # Run the tool
    result = query_tool(query="Calculate life", context="Deep thought")

    # Verify sub-agent instantiation
    MockAgentClass.assert_called_once()
    # Verify chat_turn call
    # The prompt should combine context and query
    expected_prompt = "Context:\nDeep thought\n\nTask: Calculate life"
    mock_instance.chat_turn.assert_called_with(expected_prompt)

    # Verify result
    assert result["status"] == "ok"
    assert result["answer"] == "42"


def test_rlm_query_enforces_max_depth(mock_agent):
    """Test that rlm_query respects max_depth and prevents infinite recursion."""
    MockAgentClass = MagicMock()
    mock_instance = MockAgentClass.return_value
    mock_instance.chat_turn.return_value = {"assistant_response": "test"}
    mock_instance.history.messages = []

    mock_agent.__class__ = MockAgentClass
    mock_agent._max_depth = 2
    mock_agent._current_depth = 1  # One level down already

    tools = build_tool_list(mock_agent)
    query_tool = next(t for t in tools if t.name == "rlm_query")

    result = query_tool(query="Test query")

    # Should have spawned with incremented depth
    call_args = MockAgentClass.call_args
    assert call_args.kwargs.get("current_depth") == 2  # Verify result is ok
    assert result["status"] == "ok"


def test_rlm_query_blocks_at_max_depth(mock_agent):
    """Test that rlm_query blocks when max_depth is reached."""
    mock_agent._max_depth = 2
    mock_agent._current_depth = 2  # Already at max

    tools = build_tool_list(mock_agent)
    query_tool = next(t for t in tools if t.name == "rlm_query")

    result = query_tool(query="Test query")

    # Should return error due to depth exceeded
    assert result["status"] == "error"
    assert "max recursion depth" in result["error"].lower()


def test_rlm_query_extracts_answer_correctly(mock_agent):
    """Test that rlm_query extracts answer from the correct key."""
    MockAgentClass = MagicMock()
    mock_instance = MockAgentClass.return_value
    # DSPy 3.1.3 uses 'assistant_response' key
    mock_instance.chat_turn.return_value = {"assistant_response": "The answer is 42"}
    mock_instance.history.messages = []

    mock_agent.__class__ = MockAgentClass
    mock_agent._max_depth = 2
    mock_agent._current_depth = 0

    tools = build_tool_list(mock_agent)
    query_tool = next(t for t in tools if t.name == "rlm_query")

    result = query_tool(query="What is the answer?")

    # Should extract from 'assistant_response', not 'answer'
    assert result["status"] == "ok"
    assert result["answer"] == "The answer is 42"
