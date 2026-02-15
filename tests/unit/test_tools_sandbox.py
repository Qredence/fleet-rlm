"""Unit tests for sandbox tools (edit_file)."""

from contextlib import nullcontext
from typing import Any
from unittest.mock import MagicMock

import pytest

from fleet_rlm.react.agent import RLMReActChatAgent
from fleet_rlm.react.tools import build_tool_list
import dspy


class _FakeInterpreter:
    """Mock interpreter that captures execution."""

    def __init__(self):
        self.last_code = ""
        self.last_vars = {}
        self._volume = None

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
        return nullcontext()


@pytest.fixture
def mock_agent():
    agent = MagicMock(spec=RLMReActChatAgent)
    agent.interpreter = _FakeInterpreter()
    agent.start = MagicMock()
    agent.active_alias = "active"
    agent._get_document = MagicMock(return_value="line1\nline2")
    agent.rlm_max_iterations = 30
    agent.rlm_max_llm_calls = 50
    agent.verbose = False
    agent.history_messages = MagicMock(
        return_value=[{"user_request": "u", "assistant_response": "a"}]
    )
    agent.fmt_core_memory = MagicMock(return_value="<persona>p</persona>")
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


def test_analyze_long_document_uses_runtime_module_and_keeps_response_shape(mock_agent):
    """Long-document analysis should use cached runtime module and keep top-level keys."""

    class _FakeAnalyzeModule:
        def __call__(self, **kwargs):
            return MagicMock(
                findings=["f1"],
                answer="answer",
                sections_examined=2,
                trajectory=[{"reasoning": "r"}],
                final_reasoning="done",
            )

    mock_agent.get_runtime_module = MagicMock(return_value=_FakeAnalyzeModule())

    tools = build_tool_list(mock_agent)
    analyze_tool = next(t for t in tools if t.name == "analyze_long_document")
    result = analyze_tool(query="q", alias="active", include_trajectory=True)

    mock_agent.get_runtime_module.assert_called_once_with("analyze_long_document")
    assert set(result).issuperset(
        {
            "status",
            "findings",
            "answer",
            "sections_examined",
            "doc_chars",
            "trajectory_steps",
            "trajectory",
            "final_reasoning",
        }
    )


def test_grounded_answer_returns_structured_citations(mock_agent):
    class _FakeGroundedModule:
        def __call__(self, **kwargs):
            return MagicMock(
                answer="grounded",
                citations=[
                    {
                        "source": "active",
                        "chunk_id": "0",
                        "evidence": "fact",
                        "reason": "supports",
                    }
                ],
                confidence=91,
                coverage_notes="good",
                trajectory=[{"reasoning": "r"}],
                final_reasoning="done",
            )

    mock_agent._get_document.return_value = "# H1\nA\n\n# H2\nB"
    mock_agent.get_runtime_module = MagicMock(return_value=_FakeGroundedModule())

    tools = build_tool_list(mock_agent)
    grounded_tool = next(t for t in tools if t.name == "grounded_answer")
    result = grounded_tool(query="q", include_trajectory=True)

    mock_agent.get_runtime_module.assert_called_once_with("grounded_answer")
    assert result["status"] == "ok"
    assert result["answer"] == "grounded"
    assert result["confidence"] == 91
    assert "citations" in result and result["citations"]
    citation = result["citations"][0]
    assert set(citation.keys()) == {"source", "chunk_id", "evidence", "reason"}
    assert "trajectory_steps" in result


def test_triage_incident_logs_returns_expected_shape(mock_agent):
    class _FakeTriageModule:
        def __call__(self, **kwargs):
            return MagicMock(
                severity="critical",
                probable_root_causes=["db saturation"],
                impacted_components=["api"],
                recommended_actions=["scale db"],
                time_range="10:00-10:05",
            )

    mock_agent.get_runtime_module = MagicMock(return_value=_FakeTriageModule())
    tools = build_tool_list(mock_agent)
    triage_tool = next(t for t in tools if t.name == "triage_incident_logs")
    result = triage_tool(query="why 500s?", service_context="prod")

    assert result["status"] == "ok"
    assert result["severity"] == "critical"
    assert result["probable_root_causes"] == ["db saturation"]


def test_plan_code_change_returns_expected_shape(mock_agent):
    class _FakePlanModule:
        def __call__(self, **kwargs):
            return MagicMock(
                plan_steps=["step1"],
                files_to_touch=["src/a.py"],
                validation_commands=["uv run pytest -q"],
                risks=["regression"],
            )

    mock_agent.get_runtime_module = MagicMock(return_value=_FakePlanModule())
    tools = build_tool_list(mock_agent)
    plan_tool = next(t for t in tools if t.name == "plan_code_change")
    result = plan_tool(task="add feature", repo_context="ctx", constraints="c")

    assert result["status"] == "ok"
    assert result["plan_steps"] == ["step1"]
    assert result["files_to_touch"] == ["src/a.py"]


def test_propose_core_memory_update_returns_expected_shape(mock_agent):
    class _FakeMemoryModule:
        def __call__(self, **kwargs):
            return MagicMock(
                keep=["persona"],
                update=["scratchpad"],
                remove=[],
                rationale="latest user intent changed",
            )

    mock_agent.get_runtime_module = MagicMock(return_value=_FakeMemoryModule())
    tools = build_tool_list(mock_agent)
    memory_tool = next(t for t in tools if t.name == "propose_core_memory_update")
    result = memory_tool()

    assert result["status"] == "ok"
    assert result["keep"] == ["persona"]
    assert result["update"] == ["scratchpad"]


def test_memory_tree_returns_bounded_nodes(mock_agent):
    class _FakeTreeModule:
        def __call__(self, **kwargs):
            return MagicMock(
                nodes=[
                    {
                        "path": "/data/memory/a.txt",
                        "type": "file",
                        "size_bytes": "10",
                        "depth": "1",
                    }
                ],
                total_files=1,
                total_dirs=1,
                truncated=False,
            )

    mock_agent.get_runtime_module = MagicMock(return_value=_FakeTreeModule())
    tools = build_tool_list(mock_agent)
    tree_tool = next(t for t in tools if t.name == "memory_tree")
    result = tree_tool()

    assert result["status"] == "ok"
    assert result["total_files"] == 1
    assert result["nodes"][0]["path"] == "/data/memory/a.txt"


def test_memory_action_intent_schema_and_confirmation(mock_agent):
    class _FakeTreeModule:
        def __call__(self, **kwargs):
            return MagicMock(
                nodes=[{"path": "/data/memory/tmp.txt", "type": "file"}],
                total_files=1,
                total_dirs=1,
                truncated=False,
            )

    class _FakeIntentModule:
        def __call__(self, **kwargs):
            return MagicMock(
                action_type="delete",
                target_paths=["/data/memory/tmp.txt"],
                content_plan=[],
                risk_level="high",
                requires_confirmation=True,
                rationale="destructive",
            )

    mock_agent.get_runtime_module = MagicMock(
        side_effect=[_FakeTreeModule(), _FakeIntentModule()]
    )
    tools = build_tool_list(mock_agent)
    intent_tool = next(t for t in tools if t.name == "memory_action_intent")
    result = intent_tool(user_request="delete tmp")

    assert result["status"] == "ok"
    assert result["action_type"] == "delete"
    assert result["requires_confirmation"] is True


def test_memory_structure_audit_schema(mock_agent):
    class _FakeTreeModule:
        def __call__(self, **kwargs):
            return MagicMock(
                nodes=[{"path": "/data/memory", "type": "dir"}],
                total_files=0,
                total_dirs=1,
                truncated=False,
            )

    class _FakeAuditModule:
        def __call__(self, **kwargs):
            return MagicMock(
                issues=["flat"],
                recommended_layout=["/data/memory/projects"],
                naming_conventions=["snake_case"],
                retention_rules=["archive >30d"],
                priority_fixes=["group files"],
            )

    mock_agent.get_runtime_module = MagicMock(
        side_effect=[_FakeTreeModule(), _FakeAuditModule()]
    )
    tools = build_tool_list(mock_agent)
    audit_tool = next(t for t in tools if t.name == "memory_structure_audit")
    result = audit_tool(usage_goals="organized")

    assert result["status"] == "ok"
    assert result["issues"] == ["flat"]
    assert result["priority_fixes"] == ["group files"]


def test_memory_structure_migration_plan_schema(mock_agent):
    class _FakeTreeModule:
        def __call__(self, **kwargs):
            return MagicMock(nodes=[], total_files=0, total_dirs=1, truncated=False)

    class _FakeAuditModule:
        def __call__(self, **kwargs):
            return MagicMock(
                issues=["flat"],
                recommended_layout=[],
                naming_conventions=[],
                retention_rules=[],
                priority_fixes=[],
            )

    class _FakeMigrationModule:
        def __call__(self, **kwargs):
            return MagicMock(
                operations=[
                    {
                        "op": "move",
                        "src": "/data/memory/a.txt",
                        "dst": "/data/memory/archive/a.txt",
                        "reason": "organize",
                    }
                ],
                rollback_steps=["move back"],
                verification_checks=["exists"],
                estimated_risk="medium",
            )

    # first call for audit, second for migration
    mock_agent.get_runtime_module = MagicMock(
        side_effect=[_FakeTreeModule(), _FakeAuditModule(), _FakeMigrationModule()]
    )
    tools = build_tool_list(mock_agent)
    migrate_tool = next(t for t in tools if t.name == "memory_structure_migration_plan")
    result = migrate_tool(approved_constraints="safe")

    assert result["status"] == "ok"
    assert result["operations"][0]["op"] == "move"
    assert result["estimated_risk"] == "medium"


def test_clarification_questions_schema_and_high_risk_block(mock_agent):
    class _FakeTreeModule:
        def __call__(self, **kwargs):
            return MagicMock(nodes=[], total_files=0, total_dirs=1, truncated=False)

    class _FakeClarifyModule:
        def __call__(self, **kwargs):
            return MagicMock(
                questions=["Which folder exactly?"],
                blocking_unknowns=["target folder"],
                safe_default="no-op",
                proceed_without_answer=True,
            )

    # first call for tree, second call for clarify
    mock_agent.get_runtime_module = MagicMock(
        side_effect=[_FakeTreeModule(), _FakeClarifyModule()]
    )
    tools = build_tool_list(mock_agent)
    clarify_tool = next(t for t in tools if t.name == "clarification_questions")
    result = clarify_tool(request="clean everything", operation_risk="high")

    assert result["status"] == "ok"
    assert result["questions"]
    assert result["proceed_without_answer"] is False
