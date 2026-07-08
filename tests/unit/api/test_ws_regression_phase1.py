"""WS regression tests for Phase 1 transport-neutral refactor.

Verifies:
- WS path builds ChatExecutionContext and delegates to stream_turn()
- project_chat output is byte-for-byte identical (golden)
- WS frame schema version remains 3
- Field threading preserved
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from fleet_rlm.api.events.project_chat import project_chat
from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext, TurnControls
from fleet_rlm.runtime.events import EVENT_SCHEMA_VERSION, RuntimeEvent, RuntimeEventKind

# ── Helpers ──────────────────────────────────────────────────────────────────


class _FakePreparedRuntime:
    """Minimal stand-in for PreparedChatRuntime used in test context construction."""

    def __init__(self) -> None:
        self.cfg = None
        self.planner_lm = _FakeAgent()
        self.delegate_lm = None
        self.repository = None
        self.persistence = None
        self.persistence_required = False
        self.identity_rows = None


class _FakeIdentity:
    """Minimal stand-in for NormalizedIdentity."""

    def __init__(self) -> None:
        self.tenant_claim = "tenant-1"
        self.user_claim = "user-1"
        self.email = "test@example.com"
        self.name = "Test User"


class _FakeAgent:
    """Minimal stand-in for the agent object used in stream_turn."""

    def __init__(self) -> None:
        self.execution_mode: str | None = None
        self.kwargs: dict[str, Any] | None = None

    def set_execution_mode(self, mode: str) -> None:
        self.execution_mode = mode

    async def aiter_chat_turn_stream(self, **kwargs: Any) -> AsyncIterator[RuntimeEvent]:
        self.kwargs = kwargs
        cancel_check = kwargs.get("cancel_check")
        if cancel_check is not None and cancel_check():
            return
        yield RuntimeEvent(kind=RuntimeEventKind.TEXT, text="hello")
        if cancel_check is not None and cancel_check():
            return
        yield RuntimeEvent(kind=RuntimeEventKind.DONE, text="done", payload={"history_turns": 1})


class _FakePlannerLM:
    """Planner LM stand-in that must not be used as the AgentRuntime."""

    def __init__(self) -> None:
        self.execution_mode: str | None = None

    def set_execution_mode(self, mode: str) -> None:
        self.execution_mode = mode


# ── project_chat golden tests ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("event", "expected_kind", "expected_text"),
    [
        (RuntimeEvent(kind=RuntimeEventKind.TURN_STARTED, text="started"), "execution_started", "started"),
        (RuntimeEvent(kind=RuntimeEventKind.TEXT, text="hello"), "execution_step", "hello"),
        (RuntimeEvent(kind=RuntimeEventKind.REASONING, text="thinking"), "execution_step", "thinking"),
        (RuntimeEvent(kind=RuntimeEventKind.STATUS, text="working"), "execution_step", "working"),
        (RuntimeEvent(kind=RuntimeEventKind.WARNING, text="caution"), "execution_step", "caution"),
        (
            RuntimeEvent(kind=RuntimeEventKind.DONE, text="done", payload={"status": "completed"}),
            "execution_completed",
            "done",
        ),
        (RuntimeEvent(kind=RuntimeEventKind.ERROR, text="boom"), "execution_completed", "boom"),
    ],
)
def test_project_chat_golden_kinds(
    event: RuntimeEvent,
    expected_kind: str,
    expected_text: str,
) -> None:
    """Golden test: project_chat produces expected kind/text for each event kind."""
    result = project_chat(event, sequence=1, run_id="test-run")

    assert result["version"] == EVENT_SCHEMA_VERSION, "Schema version must remain 3"
    assert result["kind"] == expected_kind, f"Expected kind={expected_kind}, got {result['kind']}"
    assert result["text"] == expected_text, f"Expected text={expected_text!r}, got {result['text']!r}"
    assert result["sequence"] == 1
    assert result["event_id"] == "test-run:1"


def test_project_chat_golden_schema_version_is_3() -> None:
    """Golden test: schema version remains 3 (VAL-REF-018)."""
    event = RuntimeEvent(kind=RuntimeEventKind.TEXT, text="hello")
    result = project_chat(event, sequence=0)
    assert result["version"] == 3, f"Expected schema version 3, got {result['version']}"
    assert EVENT_SCHEMA_VERSION == 3, "EVENT_SCHEMA_VERSION constant must be 3"


def test_project_chat_golden_tool_call_frame() -> None:
    """Golden test: tool call projection (TOOL_CALL)."""
    from fleet_rlm.runtime.events import RuntimeToolInfo

    event = RuntimeEvent(
        kind=RuntimeEventKind.TOOL_CALL,
        text="calling tool",
        tool=RuntimeToolInfo(
            tool_name="repl_execute",
            tool_args={"code": "print(1)"},
        ),
    )
    result = project_chat(event, sequence=2, run_id="run-1")

    assert result["kind"] == "execution_step"
    assert result["version"] == 3
    assert result["payload"]["tool_name"] == "repl_execute"
    assert result["payload"]["tool_args"] == {"code": "print(1)"}
    assert result["event_id"] == "run-1:2"


def test_project_chat_golden_tool_result_frame() -> None:
    """Golden test: tool result projection (TOOL_RESULT)."""
    from fleet_rlm.runtime.events import RuntimeToolInfo

    event = RuntimeEvent(
        kind=RuntimeEventKind.TOOL_RESULT,
        text="42",
        tool=RuntimeToolInfo(
            tool_name="repl_execute",
            tool_output="42",
            step_index=0,
        ),
    )
    result = project_chat(event, sequence=3, run_id="run-1")

    assert result["kind"] == "execution_step"
    assert result["version"] == 3
    assert result["payload"]["tool_name"] == "repl_execute"
    assert result["payload"]["tool_output"] == "42"
    assert result["payload"]["step_index"] == 0


def test_project_chat_golden_payload_override() -> None:
    """Golden test: payload_override merges into output."""
    event = RuntimeEvent(kind=RuntimeEventKind.STATUS, text="working", payload={"original": True})
    result = project_chat(event, sequence=0, run_id="r", payload_override={"enriched": True})

    assert result["payload"]["original"] is True
    assert result["payload"]["enriched"] is True


def test_project_chat_golden_event_id_without_run_id() -> None:
    """Golden test: event_id is just sequence when run_id is None."""
    event = RuntimeEvent(kind=RuntimeEventKind.TEXT, text="hello")
    result = project_chat(event, sequence=5, run_id=None)
    assert result["event_id"] == "5"


def test_project_chat_golden_timestamp_isoformat() -> None:
    """Golden test: timestamp is ISO-formatted."""
    from datetime import datetime, timezone

    event = RuntimeEvent(kind=RuntimeEventKind.TEXT, text="hello")
    result = project_chat(event, sequence=0)
    # Should be parseable as ISO datetime
    parsed = datetime.fromisoformat(result["timestamp"])
    assert parsed.tzinfo is not None or parsed.tzinfo == timezone.utc


# ── ChatExecutionContext construction tests ──────────────────────────────────


def test_chat_execution_context_construction() -> None:
    """ChatExecutionContext can be constructed from WS inputs (VAL-REF-014)."""
    runtime = _FakePreparedRuntime()
    identity = _FakeIdentity()
    cancel_flag: dict[str, bool] = {"cancelled": False}

    ctx = ChatExecutionContext(
        prepared=runtime,
        identity=identity,
        session_id="session-1",
        canonical_workspace_id="workspace-1",
        canonical_user_id="user-1",
        owner_tenant_claim="tenant-1",
        owner_user_claim="user-1",
        cancel_flag=cancel_flag,
        controls=TurnControls(),
    )

    assert ctx.prepared is runtime
    assert ctx.identity is identity
    assert ctx.session_id == "session-1"
    assert ctx.canonical_workspace_id == "workspace-1"
    assert ctx.canonical_user_id == "user-1"
    assert ctx.owner_tenant_claim == "tenant-1"
    assert ctx.owner_user_claim == "user-1"
    assert ctx.cancel_flag is cancel_flag
    assert isinstance(ctx.controls, TurnControls)


def test_turn_controls_defaults() -> None:
    """TurnControls defaults match contract (VAL-REF-002)."""
    controls = TurnControls()
    assert controls.execution_mode is None
    assert controls.repo_url is None
    assert controls.repo_ref is None
    assert controls.context_paths == []
    assert controls.batch_concurrency is None
    assert controls.docs_path is None
    assert controls.trace is None
    assert controls.trace_mode is None
    assert controls.selected_skill_ids == []


def test_turn_controls_field_threading() -> None:
    """TurnControls field threading preserves all WSMessage control fields (VAL-REF-021)."""
    controls = TurnControls(
        execution_mode="rlm",
        repo_url="https://github.com/example/repo",
        repo_ref="main",
        context_paths=["src/", "docs/"],
        batch_concurrency=3,
        docs_path="/path/to/docs",
        trace=True,
        trace_mode="verbose",
        selected_skill_ids=["skill-1", "skill-2"],
    )

    assert controls.execution_mode == "rlm"
    assert controls.repo_url == "https://github.com/example/repo"
    assert controls.repo_ref == "main"
    assert controls.context_paths == ["src/", "docs/"]
    assert controls.batch_concurrency == 3
    assert controls.docs_path == "/path/to/docs"
    assert controls.trace is True
    assert controls.trace_mode == "verbose"
    assert controls.selected_skill_ids == ["skill-1", "skill-2"]


# ── stream_agent_turn refactored path test ───────────────────────────────────


@pytest.mark.asyncio
async def test_stream_agent_turn_builds_context_and_delegates_to_stream_turn() -> None:
    """stream_agent_turn builds ChatExecutionContext and delegates to stream_turn
    when prepared_runtime is provided (VAL-REF-014, VAL-REF-015)."""
    from fleet_rlm.api.routers.ws.stream_events import WorkspaceTaskRequest, stream_agent_turn

    runtime = _FakePreparedRuntime()
    planner_lm = _FakePlannerLM()
    agent_runtime = _FakeAgent()
    runtime.planner_lm = planner_lm
    identity = _FakeIdentity()

    request = WorkspaceTaskRequest(
        agent=agent_runtime,
        message="hello world",
        execution_mode="rlm",
        trace=True,
        docs_path=None,
        repo_url=None,
        repo_ref=None,
        context_paths=None,
        batch_concurrency=None,
        workspace_id="workspace-1",
        cancel_check=lambda: False,
        prepare=None,
        prepared_runtime=runtime,
        identity=identity,
        session_id="session-1",
        canonical_workspace_id="workspace-1",
        canonical_user_id="user-1",
        owner_tenant_claim="tenant-1",
        owner_user_claim="user-1",
        cancel_flag={"cancelled": False},
    )

    events: list[RuntimeEvent] = []
    async for event in stream_agent_turn(request):
        events.append(event)

    # Verify the stream_turn path was taken (events from fake aiter_chat_turn_stream)
    assert len(events) == 2
    assert events[0].kind == RuntimeEventKind.TEXT
    assert events[0].text == "hello"
    assert events[1].kind == RuntimeEventKind.DONE

    # Verify execution mode was set on the AgentRuntime, not the planner LM.
    assert agent_runtime.execution_mode == "rlm"
    assert planner_lm.execution_mode is None


@pytest.mark.asyncio
async def test_stream_agent_turn_respects_cancel_flag() -> None:
    """stream_agent_turn respects cancel_flag through stream_turn path (VAL-REF-020)."""
    from fleet_rlm.api.routers.ws.stream_events import WorkspaceTaskRequest, stream_agent_turn

    runtime = _FakePreparedRuntime()
    identity = _FakeIdentity()
    cancel_flag: dict[str, bool] = {"cancelled": False}

    request = WorkspaceTaskRequest(
        agent=runtime.planner_lm,
        message="hello",
        execution_mode=None,
        prepared_runtime=runtime,
        identity=identity,
        session_id="session-1",
        canonical_workspace_id="workspace-1",
        canonical_user_id="user-1",
        owner_tenant_claim="tenant-1",
        owner_user_claim="user-1",
        cancel_flag=cancel_flag,
    )

    # Cancel before streaming
    cancel_flag["cancelled"] = True

    events: list[RuntimeEvent] = []
    async for event in stream_agent_turn(request):
        events.append(event)

    # Should be empty or minimal due to cancellation
    assert len(events) <= 1


@pytest.mark.asyncio
async def test_stream_agent_turn_field_threading_preserved() -> None:
    """All WSMessage control fields thread through to stream_turn (VAL-REF-021)."""
    from fleet_rlm.api.routers.ws.stream_events import WorkspaceTaskRequest, stream_agent_turn

    runtime = _FakePreparedRuntime()
    identity = _FakeIdentity()

    request = WorkspaceTaskRequest(
        agent=runtime.planner_lm,
        message="hello",
        execution_mode="auto",
        trace=True,
        docs_path="/docs",
        repo_url="https://github.com/example/repo",
        repo_ref="main",
        context_paths=["src/"],
        batch_concurrency=2,
        workspace_id="workspace-1",
        cancel_check=lambda: False,
        prepared_runtime=runtime,
        identity=identity,
        session_id="session-1",
        canonical_workspace_id="workspace-1",
        canonical_user_id="user-1",
        owner_tenant_claim="tenant-1",
        owner_user_claim="user-1",
        cancel_flag={"cancelled": False},
    )

    async for _ in stream_agent_turn(request):
        pass

    # Verify fields reached the agent via stream_turn
    assert runtime.planner_lm.kwargs is not None
    kwargs = runtime.planner_lm.kwargs
    assert kwargs["message"] == "hello"
    assert kwargs.get("trace") is True
    assert kwargs.get("docs_path") == "/docs"
    assert kwargs.get("repo_url") == "https://github.com/example/repo"
    assert kwargs.get("repo_ref") == "main"
    assert kwargs.get("context_paths") == ["src/"]
    assert kwargs.get("batch_concurrency") == 2


# ── WS frame schema version invariant ────────────────────────────────────────


def test_ws_frame_schema_version_remains_3() -> None:
    """All WS frames from project_chat carry schema version 3 (VAL-REF-018)."""
    for kind in RuntimeEventKind:
        event = RuntimeEvent(kind=kind, text="test")
        result = project_chat(event, sequence=0)
        assert result["version"] == 3, f"Event {kind} produced version {result['version']}, expected 3"
