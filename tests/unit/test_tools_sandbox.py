"""Unit tests for sandbox tools (edit_file)."""

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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

    def start(self):
        """Mock start method for interpreter lifecycle."""
        pass

    def shutdown(self):
        """Mock shutdown method for interpreter lifecycle."""
        pass


class _VolumeTextInterpreter(_FakeInterpreter):
    """Fake interpreter that returns text for load_from_volume submit calls."""

    def __init__(self, text: str):
        super().__init__()
        self._text = text

    def execute(
        self, code: str, variables: dict[str, Any], execution_profile: Any = None
    ):
        self.last_code = code
        self.last_vars = variables
        if "load_from_volume(path)" in code:
            return dspy.primitives.code_interpreter.FinalOutput(
                output={"status": "ok", "text": self._text}
            )
        return super().execute(code, variables, execution_profile)


@pytest.fixture
def mock_agent():
    agent = MagicMock(spec=RLMReActChatAgent)
    agent.interpreter = _FakeInterpreter()
    agent.start = MagicMock()
    agent.delegate_lm = None
    agent.active_alias = "active"
    agent._get_document = MagicMock(return_value="line1\nline2")
    agent.get_runtime_module = MagicMock()
    agent._claim_delegate_slot = MagicMock(return_value=(True, 8))
    agent._record_delegate_fallback = MagicMock()
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


def test_process_document_uses_cached_document_text(mock_agent):
    """process_document should report metadata from the loaded document cache."""
    tools = build_tool_list(mock_agent)
    process_tool = next(t for t in tools if t.name == "process_document")

    result = process_tool(path="/data/workspace/doc.txt", alias="active")

    assert result["status"] == "ok"
    assert result["alias"] == "active"
    assert result["path"] == "/data/workspace/doc.txt"
    assert result["chars"] == 0
    assert result["lines"] == 0


def test_process_document_with_non_empty_volume_payload(mock_agent):
    """process_document should report chars/lines for loaded volume text."""
    mock_agent.interpreter = _VolumeTextInterpreter("alpha\nbeta\ngamma")

    def _set_document(alias: str, content: str) -> None:
        mock_agent.documents[alias] = content

    mock_agent._set_document = MagicMock(side_effect=_set_document)

    tools = build_tool_list(mock_agent)
    process_tool = next(t for t in tools if t.name == "process_document")

    result = process_tool(path="/data/workspace/doc.txt", alias="report")

    assert result["status"] == "ok"
    assert result["alias"] == "report"
    assert result["path"] == "/data/workspace/doc.txt"
    assert result["chars"] == len("alpha\nbeta\ngamma")
    assert result["lines"] == 3


def test_rlm_query_spawns_sub_agent(mock_agent):
    """Test that rlm_query spawns a sub-agent."""
    # We need to mock the RLMReActChatAgent class effectively since rlm_query instantiates it.
    # rlm_query uses `agent.__class__`.

    # Mock the __class__ of our mock_agent to return a Mock class
    MockAgentClass = MagicMock()
    mock_instance = MockAgentClass.return_value
    # DSPy 3.1.3 uses 'assistant_response' key
    mock_instance.chat_turn.return_value = {"assistant_response": "42"}
    mock_instance.achat_turn = AsyncMock(return_value={"assistant_response": "42"})
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
    mock_instance.achat_turn.assert_called_with(expected_prompt)

    # Verify result
    assert result["status"] == "ok"
    assert result["answer"] == "42"


def test_rlm_query_enforces_max_depth(mock_agent):
    """Test that rlm_query respects max_depth and prevents infinite recursion."""
    MockAgentClass = MagicMock()
    mock_instance = MockAgentClass.return_value
    mock_instance.chat_turn.return_value = {"assistant_response": "test"}
    mock_instance.achat_turn = AsyncMock(return_value={"assistant_response": "test"})
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
    mock_instance.achat_turn = AsyncMock(
        return_value={"assistant_response": "The answer is 42"}
    )
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


def _configure_runtime_modules(
    mock_agent: MagicMock, modules: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    calls: dict[str, list[dict[str, Any]]] = {name: [] for name in modules}

    def _factory(name: str):
        if name not in modules:
            raise AssertionError(f"Unexpected runtime module: {name}")

        module_output = modules[name]

        def _module(**kwargs):
            calls[name].append(kwargs)
            if callable(module_output):
                return module_output(**kwargs)
            return module_output

        return _module

    mock_agent.get_runtime_module = MagicMock(side_effect=_factory)
    return calls


def test_analyze_long_document_uses_runtime_module_and_keeps_response_shape(mock_agent):
    """Long-document analysis should use runtime modules and keep top-level keys."""
    module_calls = _configure_runtime_modules(
        mock_agent,
        {
            "analyze_long_document": SimpleNamespace(
                findings=["f1", "f2"],
                answer="analysis result",
                sections_examined=3,
                trajectory=[{"reasoning": "step1"}],
                depth=1,
                sub_agent_history=0,
            )
        },
    )

    tools = build_tool_list(mock_agent)
    analyze_tool = next(t for t in tools if t.name == "analyze_long_document")
    result = analyze_tool(query="q", alias="active", include_trajectory=True)

    assert len(module_calls["analyze_long_document"]) == 1
    assert result["status"] == "ok"
    assert set(result).issuperset(
        {"status", "findings", "answer", "doc_chars", "depth", "sub_agent_history"}
    )
    assert result["answer"] == "analysis result"


def test_grounded_answer_uses_runtime_module_with_citations(mock_agent):
    module_calls = _configure_runtime_modules(
        mock_agent,
        {
            "grounded_answer": SimpleNamespace(
                answer="grounded answer",
                citations=[
                    {
                        "source": "doc.md",
                        "chunk_id": 1,
                        "evidence": "text",
                        "reason": "match",
                    }
                ],
                confidence=87,
                coverage_notes="covered",
                depth=1,
            )
        },
    )
    mock_agent._get_document.return_value = "# H1\nA\n\n# H2\nB"

    tools = build_tool_list(mock_agent)
    grounded_tool = next(t for t in tools if t.name == "grounded_answer")
    result = grounded_tool(query="q", include_trajectory=True)

    assert len(module_calls["grounded_answer"]) == 1
    assert result["status"] == "ok"
    assert result["answer"] == "grounded answer"
    assert result["citations"] == [
        {"source": "doc.md", "chunk_id": 1, "evidence": "text", "reason": "match"}
    ]
    assert result["confidence"] == 87
    assert result["coverage_notes"] == "covered"
    assert "depth" in result


def test_grounded_answer_rejects_invalid_max_chunks(mock_agent):
    """grounded_answer should reject non-positive max_chunks."""
    _configure_runtime_modules(mock_agent, {})
    mock_agent._get_document.return_value = "# H1\nA"

    tools = build_tool_list(mock_agent)
    grounded_tool = next(t for t in tools if t.name == "grounded_answer")
    result = grounded_tool(query="q", max_chunks=0)

    assert result["status"] == "error"


def test_triage_incident_logs_uses_runtime_module(mock_agent):
    module_calls = _configure_runtime_modules(
        mock_agent,
        {
            "triage_incident_logs": SimpleNamespace(
                severity="high",
                probable_root_causes=["database saturation"],
                impacted_components=["api"],
                recommended_actions=["scale db"],
                time_range="5m",
            )
        },
    )

    tools = build_tool_list(mock_agent)
    triage_tool = next(t for t in tools if t.name == "triage_incident_logs")
    result = triage_tool(query="why 500s?", service_context="prod")

    assert len(module_calls["triage_incident_logs"]) == 1
    assert result["status"] == "ok"
    assert "severity" in result
    assert "depth" in result


def test_plan_code_change_uses_runtime_module(mock_agent):
    module_calls = _configure_runtime_modules(
        mock_agent,
        {
            "plan_code_change": SimpleNamespace(
                plan_steps=["step1"],
                files_to_touch=["src/a.py"],
                validation_commands=["uv run pytest"],
                risks=["none"],
            )
        },
    )

    tools = build_tool_list(mock_agent)
    plan_tool = next(t for t in tools if t.name == "plan_code_change")
    result = plan_tool(task="add feature", repo_context="ctx", constraints="c")

    assert len(module_calls["plan_code_change"]) == 1
    assert result["status"] == "ok"
    assert "plan_steps" in result
    assert "depth" in result


def test_propose_core_memory_update_uses_runtime_module(mock_agent):
    module_calls = _configure_runtime_modules(
        mock_agent,
        {
            "propose_core_memory_update": SimpleNamespace(
                keep=["persona"],
                update=["scratchpad"],
                remove=[],
                rationale="new facts",
            )
        },
    )

    tools = build_tool_list(mock_agent)
    memory_tool = next(t for t in tools if t.name == "propose_core_memory_update")
    result = memory_tool()

    assert len(module_calls["propose_core_memory_update"]) == 1
    assert result["status"] == "ok"
    assert "keep" in result
    assert "update" in result
    assert "depth" in result


def test_memory_tree_uses_runtime_module(mock_agent):
    module_calls = _configure_runtime_modules(
        mock_agent,
        {
            "memory_tree": SimpleNamespace(
                nodes=[
                    {
                        "path": "/data/memory/a.txt",
                        "type": "file",
                        "size_bytes": 4,
                        "depth": 1,
                    }
                ],
                total_files=1,
                total_dirs=0,
                truncated=False,
                depth=1,
            )
        },
    )

    tools = build_tool_list(mock_agent)
    tree_tool = next(t for t in tools if t.name == "memory_tree")
    result = tree_tool()

    assert len(module_calls["memory_tree"]) == 1
    assert result["status"] == "ok"
    assert result["nodes"] == [
        {"path": "/data/memory/a.txt", "type": "file", "size_bytes": 4, "depth": 1}
    ]
    assert result["total_files"] == 1
    assert result["total_dirs"] == 0
    assert result["truncated"] is False
    assert "depth" in result


def test_memory_action_intent_uses_runtime_modules(mock_agent):
    _configure_runtime_modules(
        mock_agent,
        {
            "memory_tree": SimpleNamespace(nodes=[], total_files=0, total_dirs=0),
            "memory_action_intent": SimpleNamespace(
                action_type="delete",
                target_paths=["/data/memory/tmp.log"],
                content_plan=["remove stale file"],
                risk_level="high",
                requires_confirmation=True,
                rationale="destructive action",
            ),
        },
    )

    tools = build_tool_list(mock_agent)
    intent_tool = next(t for t in tools if t.name == "memory_action_intent")
    result = intent_tool(user_request="delete tmp")

    assert result["status"] == "ok"
    assert "action_type" in result
    assert "requires_confirmation" in result
    assert "depth" in result


def test_memory_structure_audit_uses_runtime_modules(mock_agent):
    _configure_runtime_modules(
        mock_agent,
        {
            "memory_tree": SimpleNamespace(nodes=[], total_files=0, total_dirs=0),
            "memory_structure_audit": SimpleNamespace(
                issues=["flat namespace"],
                recommended_layout=["/data/memory/projects"],
                naming_conventions=["kebab-case"],
                retention_rules=["archive stale files after 90d"],
                priority_fixes=["create project folders"],
            ),
        },
    )

    tools = build_tool_list(mock_agent)
    audit_tool = next(t for t in tools if t.name == "memory_structure_audit")
    result = audit_tool(usage_goals="organized")

    assert result["status"] == "ok"
    assert "issues" in result
    assert "priority_fixes" in result
    assert "depth" in result


def test_memory_structure_migration_plan_uses_runtime_modules(mock_agent):
    _configure_runtime_modules(
        mock_agent,
        {
            "memory_tree": SimpleNamespace(nodes=[], total_files=0, total_dirs=0),
            "memory_structure_audit": SimpleNamespace(
                issues=["flat namespace"],
                recommended_layout=[],
                naming_conventions=[],
                retention_rules=[],
                priority_fixes=[],
            ),
            "memory_structure_migration_plan": SimpleNamespace(
                operations=[{"op": "mkdir", "dst": "/data/memory/projects"}],
                rollback_steps=["rmdir /data/memory/projects"],
                verification_checks=["ls /data/memory"],
                estimated_risk="low",
            ),
        },
    )

    tools = build_tool_list(mock_agent)
    migrate_tool = next(t for t in tools if t.name == "memory_structure_migration_plan")
    result = migrate_tool(approved_constraints="safe")

    assert result["status"] == "ok"
    assert "operations" in result
    assert "estimated_risk" in result
    assert "depth" in result


def test_clarification_questions_high_risk_blocks_proceed(mock_agent):
    _configure_runtime_modules(
        mock_agent,
        {
            "memory_tree": SimpleNamespace(nodes=[], total_files=0, total_dirs=0),
            "clarification_questions": SimpleNamespace(
                questions=["Which directory should be cleaned?"],
                blocking_unknowns=["target scope"],
                safe_default="no-op",
                proceed_without_answer=True,
            ),
        },
    )

    tools = build_tool_list(mock_agent)
    clarify_tool = next(t for t in tools if t.name == "clarification_questions")
    result = clarify_tool(request="clean everything", operation_risk="high")

    assert result["status"] == "ok"
    assert result["proceed_without_answer"] is False
    assert "depth" in result


def test_delegate_tools_block_at_max_depth(mock_agent):
    """All delegate tools should return error when max depth is reached."""
    mock_agent._max_depth = 2
    mock_agent._current_depth = 2  # Already at max

    tools = build_tool_list(mock_agent)
    analyze_tool = next(t for t in tools if t.name == "analyze_long_document")
    result = analyze_tool(query="q")

    assert result["status"] == "error"
    assert "max recursion depth" in result["error"].lower()
