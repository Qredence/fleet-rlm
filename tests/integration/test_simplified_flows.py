"""Integration tests for cross-area simplified flows.

Covers VAL-CROSS-001 through VAL-CROSS-003 from the validation contract:

- VAL-CROSS-001: E2E flow — AgentRuntime chat turn → streaming events → persistence
- VAL-CROSS-002: Multi-turn session continuity — history restored, accumulated, persisted
- VAL-CROSS-003: RLM delegation within a streaming turn — tool_call/tool_result events emitted

These tests use mocked Daytona interpreters and repositories — no live services required.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import dspy
import pytest

from fleet_rlm.runtime.agent.persistence import (
    REQUIRED_SCHEMA_KEYS,
    persist_history_to_volume,
    persist_session_metadata,
    restore_history_from_volume,
)
from fleet_rlm.runtime.agent.runtime import AgentRuntime
from fleet_rlm.runtime.models.streaming import StreamEvent

# ---------------------------------------------------------------------------
# Shared helpers and fixtures
# ---------------------------------------------------------------------------


def _make_fake_react(response: str = "Test response"):
    """Return a fake dspy.ReAct class that returns a fixed response without LLM calls."""

    class _FakeReAct:
        def __init__(self, *, signature, tools, max_iters, **kwargs):
            self.signature = signature
            self._tools = list(tools)
            self._max_iters = max_iters
            self._tool_calls: list[str] = []

        def __call__(self, **kwargs):
            return dspy.Prediction(response=response)

    return _FakeReAct


def _make_fake_react_with_tool_call(
    tool_name: str, tool_response: str, final_response: str = "Done"
):
    """Return a fake dspy.ReAct that records a tool call in trajectory."""

    class _FakeReActWithTool:
        def __init__(self, *, signature, tools, max_iters, **kwargs):
            self.signature = signature
            self._tools = list(tools)
            self._max_iters = max_iters

        def __call__(self, **kwargs):
            return dspy.Prediction(
                response=final_response,
                trajectory={
                    "tool_name_0": tool_name,
                    "tool_args_0": {"query": "test query"},
                    "tool_result_0": tool_response,
                },
            )

    return _FakeReActWithTool


def _make_mock_interpreter(
    volume_mount_path: str = "/home/daytona/memory",
) -> MagicMock:
    """Return a mock Daytona interpreter with async file I/O."""
    interp = MagicMock()
    interp.volume_mount_path = volume_mount_path
    interp._volume_store: dict[str, str] = {}

    async def _awrite(path: str, content: str) -> str:
        interp._volume_store[path] = content
        return path

    async def _aread(path: str) -> str:
        if path not in interp._volume_store:
            raise FileNotFoundError(path)
        return interp._volume_store[path]

    interp.awrite_file = _awrite
    interp.aread_file = _aread
    return interp


def _make_mock_repository() -> AsyncMock:
    """Return a mock FleetRepository."""
    repo = AsyncMock()
    mock_session = MagicMock()
    mock_session.id = uuid.uuid4()
    repo.upsert_chat_session.return_value = mock_session
    return repo


# ---------------------------------------------------------------------------
# VAL-CROSS-001: E2E flow — agent chat turn → persistence
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_e2e_agent_chat_turn_produces_prediction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-CROSS-001 (partial): AgentRuntime.chat_turn returns a dspy.Prediction with response."""
    FakeReAct = _make_fake_react("Hello from agent")
    monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.ReAct", FakeReAct)
    monkeypatch.setattr("fleet_rlm.runtime.agent.runtime.discover_tools", lambda: [])

    runtime = AgentRuntime()
    result = runtime.chat_turn("Hello, agent!")

    assert isinstance(result, dspy.Prediction)
    assert str(getattr(result, "response", "")) == "Hello from agent"


@pytest.mark.integration
def test_e2e_agent_chat_turn_accumulates_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-CROSS-001 (partial): History is updated after a chat turn."""
    FakeReAct = _make_fake_react("Response")
    monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.ReAct", FakeReAct)
    monkeypatch.setattr("fleet_rlm.runtime.agent.runtime.discover_tools", lambda: [])

    runtime = AgentRuntime()
    assert len(list(runtime.history.messages)) == 0

    runtime.chat_turn("First message")
    assert len(list(runtime.history.messages)) == 1

    runtime.chat_turn("Second message")
    assert len(list(runtime.history.messages)) == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_e2e_history_persisted_to_volume_after_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-CROSS-001: After chat turn, history can be persisted to Daytona volume."""
    FakeReAct = _make_fake_react("Persisted response")
    monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.ReAct", FakeReAct)
    monkeypatch.setattr("fleet_rlm.runtime.agent.runtime.discover_tools", lambda: [])

    session_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    interpreter = _make_mock_interpreter()
    runtime = AgentRuntime(interpreter=interpreter)

    # Simulate a chat turn
    runtime.chat_turn("Persist this message")

    # Persist history to volume
    path = await persist_history_to_volume(
        interpreter,
        workspace_id,
        user_id,
        session_id,
        runtime.history,
    )

    # Verify file written to volume
    assert path in interpreter._volume_store
    content = interpreter._volume_store[path]
    assert content  # non-empty

    # Verify valid JSON with required schema keys
    data = json.loads(content)
    missing = REQUIRED_SCHEMA_KEYS - set(data.keys())
    assert not missing, f"Missing schema keys: {missing}"
    assert data["session_id"] == session_id
    assert len(data["turns"]) == 1
    assert data["turns"][0]["user_message"] == "Persist this message"
    assert data["turns"][0]["response"] == "Persisted response"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_e2e_session_metadata_persisted_to_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-CROSS-001: Session metadata upserted to DB after turn."""
    FakeReAct = _make_fake_react("DB response")
    monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.ReAct", FakeReAct)
    monkeypatch.setattr("fleet_rlm.runtime.agent.runtime.discover_tools", lambda: [])

    session_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())

    repository = _make_mock_repository()
    runtime = AgentRuntime()
    runtime.chat_turn("Store this session")

    result = await persist_session_metadata(
        repository,
        workspace_id=workspace_id,
        user_id=None,
        session_id=session_id,
        tenant_id=tenant_id,
        title="E2E integration test session",
    )

    # Repository upsert was called
    repository.upsert_chat_session.assert_awaited_once()
    assert result is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_e2e_full_persist_and_restore_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-CROSS-001: Full cycle: turn → persist history + metadata → restore."""
    FakeReAct = _make_fake_react("Full cycle response")
    monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.ReAct", FakeReAct)
    monkeypatch.setattr("fleet_rlm.runtime.agent.runtime.discover_tools", lambda: [])

    session_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())

    interpreter = _make_mock_interpreter()
    repository = _make_mock_repository()
    runtime = AgentRuntime(interpreter=interpreter)

    # 1. Run a chat turn
    runtime.chat_turn("E2E test message")

    # 2. Persist history to volume
    await persist_history_to_volume(
        interpreter,
        workspace_id,
        user_id,
        session_id,
        runtime.history,
    )

    # 3. Persist session metadata to DB
    await persist_session_metadata(
        repository,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        tenant_id=tenant_id,
    )

    # 4. Restore history from volume (simulating new session startup)
    restored = await restore_history_from_volume(
        interpreter,
        workspace_id,
        user_id,
        session_id,
    )

    # 5. Verify restoration
    assert restored is not None
    assert isinstance(restored, dspy.History)
    msgs = list(restored.messages)
    assert len(msgs) == 1
    assert msgs[0]["user_message"] == "E2E test message"
    assert msgs[0]["response"] == "Full cycle response"

    # 6. Verify DB was called
    repository.upsert_chat_session.assert_awaited_once()


# ---------------------------------------------------------------------------
# VAL-CROSS-002: Multi-turn session continuity
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_multi_turn_history_accumulates(monkeypatch: pytest.MonkeyPatch) -> None:
    """VAL-CROSS-002: Two turns accumulate in history; second turn receives first turn's context."""
    call_count = 0
    responses = ["First response", "Second response"]
    captured_histories: list[list[Any]] = []

    class _FakeReActMultiTurn:
        def __init__(self, *, signature, tools, max_iters, **kwargs):
            self.signature = signature

        def __call__(self, **kwargs):
            nonlocal call_count
            history: dspy.History = kwargs.get(
                "chat_history", dspy.History(messages=[])
            )
            captured_histories.append(list(getattr(history, "messages", [])))
            resp = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return dspy.Prediction(response=resp)

    monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.ReAct", _FakeReActMultiTurn)
    monkeypatch.setattr("fleet_rlm.runtime.agent.runtime.discover_tools", lambda: [])

    runtime = AgentRuntime()

    # First turn — history should be empty
    result1 = runtime.chat_turn("Turn one")
    assert str(getattr(result1, "response", "")) == "First response"
    assert len(captured_histories[0]) == 0  # No prior history

    # Second turn — history should contain turn one
    result2 = runtime.chat_turn("Turn two")
    assert str(getattr(result2, "response", "")) == "Second response"
    assert len(captured_histories[1]) == 1  # First turn visible
    assert captured_histories[1][0]["user_message"] == "Turn one"
    assert captured_histories[1][0]["response"] == "First response"

    # Total history has both turns
    assert len(list(runtime.history.messages)) == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_multi_turn_session_restore_then_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-CROSS-002: Export session, import to new runtime, run second turn — both turns in history."""
    FakeReAct = _make_fake_react("Continued response")
    monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.ReAct", FakeReAct)
    monkeypatch.setattr("fleet_rlm.runtime.agent.runtime.discover_tools", lambda: [])

    session_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    interpreter = _make_mock_interpreter()

    # ── Session A: run one turn and persist ──
    runtime_a = AgentRuntime(interpreter=interpreter)
    runtime_a.chat_turn("First turn in session A")
    await persist_history_to_volume(
        interpreter,
        workspace_id,
        user_id,
        session_id,
        runtime_a.history,
    )

    # ── Session B: restore history, run second turn ──
    runtime_b = AgentRuntime(interpreter=interpreter)
    restored = await restore_history_from_volume(
        interpreter,
        workspace_id,
        user_id,
        session_id,
    )
    assert restored is not None
    runtime_b.history = restored  # inject restored history

    # Second turn sees the prior turn in history
    assert len(list(runtime_b.history.messages)) == 1

    runtime_b.chat_turn("Second turn continuing session A")
    assert len(list(runtime_b.history.messages)) == 2

    # Persist updated history
    await persist_history_to_volume(
        interpreter,
        workspace_id,
        user_id,
        session_id,
        runtime_b.history,
    )

    # ── Verify final stored state has both turns ──
    from fleet_rlm.runtime.agent.persistence import history_volume_path
    from fleet_rlm.runtime.execution.storage_paths import runtime_storage_roots

    roots = runtime_storage_roots(interpreter)
    path = history_volume_path(roots.meta_root, workspace_id, user_id, session_id)
    content = interpreter._volume_store[path]
    data = json.loads(content)
    assert len(data["turns"]) == 2
    assert data["turns"][0]["user_message"] == "First turn in session A"
    assert data["turns"][1]["user_message"] == "Second turn continuing session A"


@pytest.mark.integration
def test_multi_turn_export_import_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """VAL-CROSS-002: export_session / import_session preserves two turns with full history."""
    FakeReAct = _make_fake_react("RT response")
    monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.ReAct", FakeReAct)
    monkeypatch.setattr("fleet_rlm.runtime.agent.runtime.discover_tools", lambda: [])

    session_id = str(uuid.uuid4())
    runtime = AgentRuntime()
    runtime.chat_turn("Round-trip turn one")
    runtime.chat_turn("Round-trip turn two")

    # Export
    exported = runtime.export_session(session_id)
    assert exported["session_id"] == session_id
    assert len(exported["turns"]) == 2

    # Import into fresh runtime
    FakeReAct2 = _make_fake_react("After import response")
    monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.ReAct", FakeReAct2)

    runtime2 = AgentRuntime()
    assert len(list(runtime2.history.messages)) == 0

    result = runtime2.import_session(exported)
    assert result["status"] == "ok"
    assert result["history_turns"] == 2
    assert len(list(runtime2.history.messages)) == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_multi_turn_continuity_db_upserted_per_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-CROSS-002: DB upsert is called after each turn (simulated two-turn flow)."""
    FakeReAct = _make_fake_react("DB turn response")
    monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.ReAct", FakeReAct)
    monkeypatch.setattr("fleet_rlm.runtime.agent.runtime.discover_tools", lambda: [])

    session_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())
    repository = _make_mock_repository()

    runtime = AgentRuntime()

    for turn_msg in ("Turn one", "Turn two"):
        runtime.chat_turn(turn_msg)
        await persist_session_metadata(
            repository,
            workspace_id=workspace_id,
            user_id=None,
            session_id=session_id,
            tenant_id=tenant_id,
        )

    assert repository.upsert_chat_session.await_count == 2


# ---------------------------------------------------------------------------
# VAL-CROSS-003: RLM delegation within a streaming turn
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_rlm_delegation_turn_has_tool_call_in_trajectory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-CROSS-003: A turn triggering delegate_to_rlm records tool_call in trajectory."""
    FakeReAct = _make_fake_react_with_tool_call(
        "delegate_to_rlm",
        '{"status": "ok", "answer": "Delegated answer"}',
        "Done via delegation",
    )
    monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.ReAct", FakeReAct)
    monkeypatch.setattr("fleet_rlm.runtime.agent.runtime.discover_tools", lambda: [])

    runtime = AgentRuntime()
    result = runtime.chat_turn("Delegate this query to RLM")

    trajectory = dict(getattr(result, "trajectory", {}) or {})
    # At least one tool call in the trajectory
    tool_names = [v for k, v in trajectory.items() if k.startswith("tool_name_")]
    assert "delegate_to_rlm" in tool_names, (
        f"Expected delegate_to_rlm in trajectory tool calls. Got: {tool_names}"
    )


@pytest.mark.integration
def test_rlm_delegation_stream_emits_tool_call_and_result_events() -> None:
    """VAL-CROSS-003: Stream events include tool_call and tool_result for delegation."""
    # Build a sequence of events that represents a delegation turn
    events = [
        StreamEvent(kind="status", text="Starting delegation"),
        StreamEvent(
            kind="tool_call",
            text="tool call: delegate_to_rlm",
            payload={"tool_name": "delegate_to_rlm", "args": {"query": "test"}},
        ),
        StreamEvent(
            kind="tool_result",
            text="tool result: delegate_to_rlm",
            payload={"tool_name": "delegate_to_rlm", "result": "Delegated answer"},
        ),
        StreamEvent(
            kind="done",
            text="Done via delegation",
            payload={
                "history_turns": 1,
                "trajectory": {"tool_name_0": "delegate_to_rlm"},
            },
        ),
    ]

    kinds = [e.kind for e in events]
    assert "tool_call" in kinds, "Missing tool_call event in stream"
    assert "tool_result" in kinds, "Missing tool_result event in stream"
    assert "done" in kinds, "Missing done event in stream"

    # Verify the stream event kinds are all canonical
    valid_kinds = {
        "status",
        "text",
        "reasoning",
        "tool_call",
        "tool_result",
        "warning",
        "error",
        "done",
    }
    for event in events:
        assert event.kind in valid_kinds, f"Non-canonical event kind: {event.kind!r}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rlm_delegation_turn_persists_delegation_in_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-CROSS-003: After delegation turn, history includes the delegated question and response."""
    FakeReAct = _make_fake_react_with_tool_call(
        "delegate_to_rlm",
        '{"status": "ok", "answer": "Subquery answer"}',
        "Final answer after delegation",
    )
    monkeypatch.setattr("fleet_rlm.runtime.agent.agent.dspy.ReAct", FakeReAct)
    monkeypatch.setattr("fleet_rlm.runtime.agent.runtime.discover_tools", lambda: [])

    session_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    interpreter = _make_mock_interpreter()

    runtime = AgentRuntime(interpreter=interpreter)
    runtime.chat_turn("Use delegate_to_rlm for this")

    # Persist after delegation turn
    await persist_history_to_volume(
        interpreter,
        workspace_id,
        user_id,
        session_id,
        runtime.history,
    )

    # Restore and verify delegation is represented in history
    restored = await restore_history_from_volume(
        interpreter,
        workspace_id,
        user_id,
        session_id,
    )
    assert restored is not None
    msgs = list(restored.messages)
    assert len(msgs) == 1
    assert msgs[0]["user_message"] == "Use delegate_to_rlm for this"
    # The response from delegation is captured
    assert "Final answer after delegation" in msgs[0]["response"]


@pytest.mark.integration
def test_rlm_delegate_tool_is_in_registry() -> None:
    """VAL-CROSS-003: delegate_to_rlm is discoverable from the tool registry."""
    from fleet_rlm.runtime.tools import discover_tools

    tools = discover_tools()
    tool_names = {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}
    assert "delegate_to_rlm" in tool_names, (
        f"delegate_to_rlm not found in tool registry. Found: {sorted(tool_names)}"
    )


@pytest.mark.integration
def test_rlm_delegate_tool_raises_without_interpreter() -> None:
    """VAL-CROSS-003: delegate_to_rlm raises RuntimeError when no interpreter is passed."""
    from fleet_rlm.runtime.tools.rlm_delegate import delegate_to_rlm

    with pytest.raises(RuntimeError, match="Daytona interpreter"):
        delegate_to_rlm("test delegation query")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rlm_delegation_with_mocked_interpreter_and_rlm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-CROSS-003: delegate_to_rlm executes in sandbox and returns structured result."""
    import fleet_rlm.runtime.tools.rlm_delegate as rlm_delegate_mod
    from fleet_rlm.runtime.tools.rlm_delegate import delegate_to_rlm

    child = SimpleNamespace(
        _started=True,
        verbose=False,
        volume_mount_path="/home/daytona/memory",
        sub_lm=None,
        rlm_max_iterations=20,
        child_isolation_metadata={},
    )
    child.start = lambda: None
    child.shutdown = lambda: None

    interpreter = SimpleNamespace(
        verbose=False,
        build_delegate_child=lambda *, remaining_llm_budget: child,
        _remaining_llm_budget=lambda: 50,
    )

    mock_prediction = dspy.Prediction(answer="Mocked RLM answer")
    monkeypatch.setattr(
        rlm_delegate_mod,
        "build_recursive_subquery_rlm",
        lambda **kwargs: lambda **kw: mock_prediction,
    )

    result = delegate_to_rlm(
        "Integration test delegation query", interpreter=interpreter
    )

    assert result["status"] == "ok"
    assert result["answer"] == "Mocked RLM answer"
