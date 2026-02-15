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
    mock_instance.chat_turn.return_value = {"answer": "42"}
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
