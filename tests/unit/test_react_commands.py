from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.core.agent.commands import _resolve_tool, execute_command


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

        def grounded_answer(**kwargs):
            self.calls.append(("grounded_answer", kwargs))
            return {"status": "ok"}

        def triage_incident_logs(**kwargs):
            self.calls.append(("triage_incident_logs", kwargs))
            return {"status": "ok"}

        def plan_code_change(**kwargs):
            self.calls.append(("plan_code_change", kwargs))
            return {"status": "ok"}

        def propose_core_memory_update(**kwargs):
            self.calls.append(("propose_core_memory_update", kwargs))
            return {"status": "ok"}

        def memory_tree(**kwargs):
            self.calls.append(("memory_tree", kwargs))
            return {"status": "ok"}

        def memory_action_intent(**kwargs):
            self.calls.append(("memory_action_intent", kwargs))
            return {"status": "ok"}

        def memory_structure_audit(**kwargs):
            self.calls.append(("memory_structure_audit", kwargs))
            return {"status": "ok"}

        def memory_structure_migration_plan(**kwargs):
            self.calls.append(("memory_structure_migration_plan", kwargs))
            return {"status": "ok"}

        def clarification_questions(**kwargs):
            self.calls.append(("clarification_questions", kwargs))
            return {"status": "ok"}

        self.react_tools = [
            analyze_long_document,
            write_to_file,
            edit_core_memory,
            grounded_answer,
            triage_incident_logs,
            plan_code_change,
            propose_core_memory_update,
            memory_tree,
            memory_action_intent,
            memory_structure_audit,
            memory_structure_migration_plan,
            clarification_questions,
        ]


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


@pytest.mark.asyncio
async def test_execute_command_dispatches_grounded_answer():
    agent = _FakeAgent()
    result = await execute_command(
        agent,
        "grounded_answer",
        {"query": "q", "chunk_strategy": "headers", "max_chunks": 4},
    )
    assert result["status"] == "ok"
    tool_name, kwargs = agent.calls[0]
    assert tool_name == "grounded_answer"
    assert kwargs["query"] == "q"


@pytest.mark.asyncio
async def test_execute_command_dispatches_triage_logs():
    agent = _FakeAgent()
    result = await execute_command(
        agent,
        "triage_logs",
        {"query": "err", "service_context": "prod"},
    )
    assert result["status"] == "ok"
    tool_name, kwargs = agent.calls[0]
    assert tool_name == "triage_incident_logs"
    assert kwargs["service_context"] == "prod"


@pytest.mark.asyncio
async def test_execute_command_dispatches_plan_code_change():
    agent = _FakeAgent()
    result = await execute_command(
        agent,
        "plan_code_change",
        {"task": "add feature", "repo_context": "ctx"},
    )
    assert result["status"] == "ok"
    tool_name, kwargs = agent.calls[0]
    assert tool_name == "plan_code_change"
    assert kwargs["task"] == "add feature"


@pytest.mark.asyncio
async def test_execute_command_dispatches_propose_memory_update():
    agent = _FakeAgent()
    result = await execute_command(agent, "propose_memory_update", {})
    assert result["status"] == "ok"
    tool_name, _ = agent.calls[0]
    assert tool_name == "propose_core_memory_update"


@pytest.mark.asyncio
async def test_execute_command_dispatches_memory_tree():
    agent = _FakeAgent()
    result = await execute_command(
        agent,
        "memory_tree",
        {"root_path": "/data/memory", "max_depth": 2, "include_hidden": False},
    )
    assert result["status"] == "ok"
    tool_name, kwargs = agent.calls[0]
    assert tool_name == "memory_tree"
    assert kwargs["max_depth"] == 2


@pytest.mark.asyncio
async def test_execute_command_dispatches_memory_action_intent():
    agent = _FakeAgent()
    result = await execute_command(
        agent,
        "memory_action_intent",
        {"user_request": "archive old files", "policy_constraints": "safe only"},
    )
    assert result["status"] == "ok"
    tool_name, kwargs = agent.calls[0]
    assert tool_name == "memory_action_intent"
    assert kwargs["user_request"] == "archive old files"


@pytest.mark.asyncio
async def test_execute_command_dispatches_memory_structure_audit():
    agent = _FakeAgent()
    result = await execute_command(
        agent,
        "memory_structure_audit",
        {"usage_goals": "keep organized"},
    )
    assert result["status"] == "ok"
    tool_name, kwargs = agent.calls[0]
    assert tool_name == "memory_structure_audit"
    assert kwargs["usage_goals"] == "keep organized"


@pytest.mark.asyncio
async def test_execute_command_dispatches_memory_structure_migration_plan():
    agent = _FakeAgent()
    result = await execute_command(
        agent,
        "memory_structure_migration_plan",
        {"approved_constraints": "no delete"},
    )
    assert result["status"] == "ok"
    tool_name, kwargs = agent.calls[0]
    assert tool_name == "memory_structure_migration_plan"
    assert kwargs["approved_constraints"] == "no delete"


@pytest.mark.asyncio
async def test_execute_command_dispatches_clarification_questions():
    agent = _FakeAgent()
    result = await execute_command(
        agent,
        "clarification_questions",
        {"request": "clean files", "operation_risk": "high"},
    )
    assert result["status"] == "ok"
    tool_name, kwargs = agent.calls[0]
    assert tool_name == "clarification_questions"
    assert kwargs["operation_risk"] == "high"


def test_resolve_tool_supports_wrapped_tools():
    agent = _FakeAgent()

    def wrapped_fn(**kwargs):
        return {"status": "ok", "kwargs": kwargs}

    agent.react_tools = [SimpleNamespace(name="analyze_long_document", func=wrapped_fn)]
    resolved = _resolve_tool(agent, "analyze_long_document")
    assert resolved is wrapped_fn


def test_resolve_tool_supports_raw_callables():
    agent = _FakeAgent()
    resolved = _resolve_tool(agent, "write_to_file")
    assert callable(resolved)
    assert resolved(path="p", content="c", append=False)["status"] == "ok"


def test_resolve_tool_falls_back_to_agent_method():
    agent = _FakeAgent()
    agent.react_tools = []

    def reset(**kwargs):
        return {"status": "ok", "kwargs": kwargs}

    agent.reset = reset
    resolved = _resolve_tool(agent, "reset")
    assert resolved is reset
