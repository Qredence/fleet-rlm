"""Tests for stream_turn — the transport-neutral async generator seam.

Covers validation assertions:
  VAL-REF-005, VAL-REF-006, VAL-REF-007, VAL-REF-008,
  VAL-REF-009, VAL-REF-010, VAL-REF-022, VAL-REF-023,
  VAL-REF-033, VAL-REF-035
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext, TurnControls
from fleet_rlm.api.runtime_services.chat_runtime import PreparedChatRuntime
from fleet_rlm.api.runtime_services.stream_turn import _build_stream_kwargs, stream_turn
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind

# ---------------------------------------------------------------------------
# Stub agent
# ---------------------------------------------------------------------------


class _StubAgent:
    """Minimal agent stub that records calls and yields canned events."""

    def __init__(self, events: list[RuntimeEvent] | None = None) -> None:
        self.events: list[RuntimeEvent] = events or [
            RuntimeEvent.status("working"),
            RuntimeEvent(kind=RuntimeEventKind.DONE, text="done", payload={"history_turns": 1}),
        ]
        self.execution_mode: str | None = None
        self.captured_kwargs: dict[str, Any] | None = None
        self.set_execution_mode_calls: list[str] = []
        self.aimport_session_state_calls: list[dict[str, Any]] = []
        self.areset_calls: int = 0

    def set_execution_mode(self, mode: str) -> None:
        self.set_execution_mode_calls.append(mode)
        self.execution_mode = mode

    async def aiter_chat_turn_stream(self, **kwargs: Any) -> AsyncIterator[RuntimeEvent]:
        self.captured_kwargs = kwargs
        for event in self.events:
            yield event

    async def aimport_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        self.aimport_session_state_calls.append(state)
        return {"status": "ok"}

    async def areset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        self.areset_calls += 1
        return {"status": "ok"}


class _NeverYieldsAgent(_StubAgent):
    """Agent that never yields — used for early-cancellation tests."""

    def __init__(self) -> None:
        super().__init__(events=[])

    async def aiter_chat_turn_stream(self, **kwargs: Any) -> AsyncIterator[RuntimeEvent]:
        self.captured_kwargs = kwargs
        # Simulate early cancellation by the runtime: check the cancel_check.
        cancel_check = kwargs.get("cancel_check")
        if cancel_check is not None and cancel_check():
            yield RuntimeEvent(
                kind=RuntimeEventKind.DONE,
                text="[cancelled]",
                payload={"cancelled": True, "history_turns": 0},
            )
            return
        return
        yield  # pragma: no cover


class _MidStreamCancellingAgent(_StubAgent):
    """Agent that yields up to N events then simulates cancellation."""

    def __init__(self, max_before_cancel: int = 2) -> None:
        super().__init__(events=[])
        self.max_before_cancel = max_before_cancel
        self.captured_kwargs = None

    async def aiter_chat_turn_stream(self, **kwargs: Any) -> AsyncIterator[RuntimeEvent]:
        self.captured_kwargs = kwargs
        cancel_check = kwargs.get("cancel_check")
        for i in range(self.max_before_cancel):
            # Check for cancellation after each yield.
            if cancel_check is not None and cancel_check():
                break
            yield RuntimeEvent.status(f"step {i}")
        # If cancelled after the loop, yield a terminal event.
        if cancel_check is not None and cancel_check():
            yield RuntimeEvent(
                kind=RuntimeEventKind.DONE,
                text="[cancelled]",
                payload={"cancelled": True, "history_turns": self.max_before_cancel},
            )


# ---------------------------------------------------------------------------
# Stub repository / persistence for session restoration
# ---------------------------------------------------------------------------


class _SessionRestoringStore:
    """Stub persistence that returns a canned session record."""

    def __init__(self, session_record: dict[str, Any] | None = None) -> None:
        self.session_record = session_record

    async def get_session_record(self, session_id: str) -> dict[str, Any] | None:
        return self.session_record


class _EmptyStore:
    """Stub persistence that returns no session."""

    async def get_session_record(self, session_id: str) -> None:
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_agent() -> _StubAgent:
    return _StubAgent()


@pytest.fixture
def sample_prepared(stub_agent: _StubAgent) -> PreparedChatRuntime:
    """Minimal PreparedChatRuntime whose planner_lm IS the stub agent."""
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        ws_default_workspace_id="default",
        ws_default_user_id="anonymous",
    )
    return PreparedChatRuntime(
        cfg=cfg,  # type: ignore[arg-type]
        planner_lm=stub_agent,  # In production, transport layer sets this to AgentRuntime
        delegate_lm=None,
        repository=object(),
        persistence=None,
        persistence_required=False,
        identity_rows=None,
    )


@pytest.fixture
def sample_context(
    sample_prepared: PreparedChatRuntime,
) -> ChatExecutionContext:
    """Default ChatExecutionContext with no session and default TurnControls."""
    from fleet_rlm.api.auth.types import NormalizedIdentity

    return ChatExecutionContext(
        prepared=sample_prepared,
        identity=NormalizedIdentity(
            tenant_claim="tenant-abc",
            user_claim="user-xyz",
            email="test@example.com",
        ),
        session_id=None,
        canonical_workspace_id="workspace-abc",
        canonical_user_id="user-xyz",
        owner_tenant_claim="tenant-abc",
        owner_user_claim="user-xyz",
        cancel_flag={"cancelled": False},
        controls=TurnControls(),
    )


# ---------------------------------------------------------------------------
# VAL-REF-005 — stream_turn is an async generator importable without
#               WebSocket/Request
# ---------------------------------------------------------------------------


class TestStreamTurnIsAsyncGenerator:
    """VAL-REF-005: stream_turn is importable and is an async generator."""

    def test_importable(self) -> None:
        """stream_turn can be imported from its module."""
        from fleet_rlm.api.runtime_services.stream_turn import stream_turn as st

        assert st is stream_turn

    def test_is_async_generator_function(self) -> None:
        """stream_turn is an async generator function (async def with yield)."""
        import inspect

        assert inspect.isasyncgenfunction(stream_turn)

    def test_callable_without_websocket_request(self) -> None:
        """stream_turn signature is (ctx, message) only — no WebSocket/Request."""
        import inspect

        sig = inspect.signature(stream_turn)
        param_names = list(sig.parameters.keys())
        assert param_names == ["ctx", "message"]

    def test_no_transport_imports_in_source(self) -> None:
        """stream_turn module does not import WebSocket or Request."""
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        source_path = (
            repo_root / "src" / "fleet_rlm" / "api" / "runtime_services" / "stream_turn.py"
        )
        source_text = source_path.read_text("utf-8")

        forbidden = [
            "fastapi.WebSocket",
            "starlette.websockets",
            "fastapi.Request",
            "starlette.requests",
        ]
        for pattern in forbidden:
            assert pattern not in source_text, (
                f"stream_turn.py must not import {pattern}"
            )

    def test_no_import_time_side_effects(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Importing stream_turn triggers no network access."""
        import importlib
        import socket

        def _fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError("unexpected socket call during import")

        monkeypatch.setattr(socket, "socket", _fail)
        monkeypatch.setattr(socket, "create_connection", _fail)  # type: ignore[attr-defined]
        monkeypatch.setattr(socket, "getaddrinfo", _fail)

        from fleet_rlm.api.runtime_services import stream_turn as mod

        importlib.reload(mod)


# ---------------------------------------------------------------------------
# VAL-REF-006 — stream_turn yields RuntimeEvent objects
# ---------------------------------------------------------------------------


class TestYieldsRuntimeEvent:
    """VAL-REF-006: stream_turn yields only RuntimeEvent instances."""

    @pytest.mark.asyncio
    async def test_yields_runtime_event_instances(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """Every yielded item is a RuntimeEvent instance."""
        events: list[RuntimeEvent] = []
        async for event in stream_turn(sample_context, "hello"):
            events.append(event)

        assert len(events) >= 1
        for event in events:
            assert isinstance(event, RuntimeEvent), (
                f"Expected RuntimeEvent, got {type(event)}: {event}"
            )

    @pytest.mark.asyncio
    async def test_no_bare_dicts_or_tuples(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """No bare dict/tuple/str yielded — only RuntimeEvent."""
        async for event in stream_turn(sample_context, "hello"):
            assert isinstance(event, RuntimeEvent)


# ---------------------------------------------------------------------------
# VAL-REF-008 — stream_turn delegates to AgentRuntime.aiter_chat_turn_stream
# ---------------------------------------------------------------------------


class TestDelegatesToRuntime:
    """VAL-REF-008: stream_turn calls aiter_chat_turn_stream once with
    cancel_check that reads ctx.cancel_flag."""

    @pytest.mark.asyncio
    async def test_calls_aiter_chat_turn_stream_once(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: _StubAgent,
    ) -> None:
        """stream_turn delegates to agent.aiter_chat_turn_stream once."""
        events = [e async for e in stream_turn(sample_context, "hello")]

        assert stub_agent.captured_kwargs is not None
        assert stub_agent.captured_kwargs["message"] == "hello"
        assert len(events) == len(stub_agent.events)

    @pytest.mark.asyncio
    async def test_cancel_check_reads_cancel_flag(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: _StubAgent,
    ) -> None:
        """The cancel_check lambda returns ctx.cancel_flag.get('cancelled', False)."""
        events = [e async for e in stream_turn(sample_context, "hello")]

        assert stub_agent.captured_kwargs is not None
        cancel_check = stub_agent.captured_kwargs["cancel_check"]
        assert callable(cancel_check)

        # Flag not set → cancel_check returns False.
        assert cancel_check() is False

        # Flag set → cancel_check returns True.
        sample_context.cancel_flag["cancelled"] = True
        assert cancel_check() is True

        # Length check — events collected before flag was set.
        assert len(events) == len(stub_agent.events)

    @pytest.mark.asyncio
    async def test_cancel_check_not_called_with_flag_set(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """cancel_check is a lambda so the runtime polls it at its own pace."""
        sample_context.cancel_flag["cancelled"] = True
        agent = sample_context.prepared.planner_lm
        assert isinstance(agent, _StubAgent)

        [e async for e in stream_turn(sample_context, "hello")]

        assert agent.captured_kwargs is not None
        cancel_check = agent.captured_kwargs["cancel_check"]
        assert cancel_check() is True


# ---------------------------------------------------------------------------
# VAL-REF-009 — stream_turn threads TurnControls fields to runtime
# ---------------------------------------------------------------------------


class TestThreadsTurnControls:
    """VAL-REF-009: Non-None TurnControls fields thread into aiter_chat_turn_stream kwargs."""

    @pytest.mark.asyncio
    async def test_all_fields_threaded_when_set(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """All non-None TurnControls fields appear in kwargs."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        controls = TurnControls(
            execution_mode="rlm",
            repo_url="https://example.com/repo.git",
            repo_ref="main",
            context_paths=["src/"],
            batch_concurrency=3,
            docs_path="./docs",
            trace=True,
            trace_mode="full",
            selected_skill_ids=["skill-a", "skill-b"],
        )
        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=NormalizedIdentity(  # type: ignore[arg-type]
                tenant_claim="t", user_claim="u", email="t@t.com"
            ),
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=controls,
        )
        agent = ctx.prepared.planner_lm
        assert isinstance(agent, _StubAgent)

        _ = [e async for e in stream_turn(ctx, "hello")]

        assert agent.captured_kwargs is not None
        # execution_mode is handled by set_execution_mode, not kwargs.
        for key in ("repo_url", "repo_ref", "context_paths", "batch_concurrency", "docs_path", "trace", "trace_mode", "selected_skill_ids"):
            expected = getattr(controls, key)
            if expected is not None:
                assert key in agent.captured_kwargs, (
                    f"Expected {key}={expected!r} in kwargs, got {agent.captured_kwargs}"
                )
                actual = agent.captured_kwargs[key]
                if isinstance(expected, list):
                    assert list(actual) == expected
                else:
                    assert actual == expected

    @pytest.mark.asyncio
    async def test_none_fields_not_forwarded(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: _StubAgent,
    ) -> None:
        """None/empty fields are not forwarded as non-None."""
        _ = [e async for e in stream_turn(sample_context, "hello")]

        assert stub_agent.captured_kwargs is not None
        # TurnControls fields that are None should not be in kwargs.
        # (execution_mode is handled via set_execution_mode)
        assert "trace" not in stub_agent.captured_kwargs or stub_agent.captured_kwargs["trace"] is None
        # context_paths default is [] — empty so not forwarded.
        assert "context_paths" not in stub_agent.captured_kwargs
        # selected_skill_ids default is [] — empty so not forwarded.
        assert "selected_skill_ids" not in stub_agent.captured_kwargs

    @pytest.mark.asyncio
    async def test_context_paths_copy_not_mutated(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: _StubAgent,
    ) -> None:
        """context_paths is copied (list()) so original isn't mutated."""
        sample_context.controls.context_paths = ["src/", "lib/"]
        _ = [e async for e in stream_turn(sample_context, "hello")]

        assert stub_agent.captured_kwargs is not None
        assert stub_agent.captured_kwargs["context_paths"] == ["src/", "lib/"]

        # Mutating the original doesn't affect kwargs.
        sample_context.controls.context_paths.append("extra/")
        assert stub_agent.captured_kwargs["context_paths"] == ["src/", "lib/"]

    @pytest.mark.asyncio
    async def test_selected_skill_ids_copied(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: _StubAgent,
    ) -> None:
        """selected_skill_ids is copied so original isn't mutated."""
        sample_context.controls.selected_skill_ids = ["skill-a"]
        _ = [e async for e in stream_turn(sample_context, "hello")]

        assert stub_agent.captured_kwargs is not None
        assert stub_agent.captured_kwargs["selected_skill_ids"] == ["skill-a"]

        sample_context.controls.selected_skill_ids.append("skill-b")
        assert stub_agent.captured_kwargs["selected_skill_ids"] == ["skill-a"]


# ---------------------------------------------------------------------------
# VAL-REF-010 — stream_turn sets execution_mode from TurnControls
# ---------------------------------------------------------------------------


class TestSetsExecutionMode:
    """VAL-REF-010: set_execution_mode called when controls.execution_mode
    is not None; not called when None."""

    @pytest.mark.asyncio
    async def test_sets_execution_mode_when_not_none(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """set_execution_mode called with the value when execution_mode is set."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u", email="t@t.com"),  # type: ignore[arg-type]
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(execution_mode="simple"),
        )
        agent = ctx.prepared.planner_lm
        assert isinstance(agent, _StubAgent)

        _ = [e async for e in stream_turn(ctx, "hello")]

        assert agent.set_execution_mode_calls == ["simple"]
        assert agent.execution_mode == "simple"

    @pytest.mark.asyncio
    async def test_no_set_execution_mode_when_none(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: _StubAgent,
    ) -> None:
        """set_execution_mode not called when execution_mode is None."""
        assert sample_context.controls.execution_mode is None

        _ = [e async for e in stream_turn(sample_context, "hello")]

        assert stub_agent.set_execution_mode_calls == []


# ---------------------------------------------------------------------------
# VAL-REF-022 — Session restoration via session_id
# ---------------------------------------------------------------------------


class TestSessionRestoration:
    """VAL-REF-022: session_id not None restores session; None starts fresh."""

    @pytest.mark.asyncio
    async def test_restores_session_when_session_id_not_none(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """Non-None session_id triggers session restoration via aimport_session_state."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        session_state = {"history_turns": 3, "conversation": ["hi", "hello"]}
        store = _SessionRestoringStore(
            session_record={"session": {"state": session_state}}
        )

        prepared = PreparedChatRuntime(
            cfg=sample_prepared.cfg,
            planner_lm=sample_prepared.planner_lm,
            delegate_lm=sample_prepared.delegate_lm,
            repository=None,
            persistence=store,
            persistence_required=False,
            identity_rows=sample_prepared.identity_rows,
        )

        ctx = ChatExecutionContext(
            prepared=prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u", email="t@t.com"),  # type: ignore[arg-type]
            session_id="session-123",
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(),
        )
        agent = ctx.prepared.planner_lm
        assert isinstance(agent, _StubAgent)

        _ = [e async for e in stream_turn(ctx, "hello")]

        assert len(agent.aimport_session_state_calls) == 1
        assert agent.aimport_session_state_calls[0] == session_state

    @pytest.mark.asyncio
    async def test_no_restore_when_session_id_none(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: _StubAgent,
    ) -> None:
        """None session_id does not trigger session restoration."""
        assert sample_context.session_id is None

        _ = [e async for e in stream_turn(sample_context, "hello")]

        assert stub_agent.aimport_session_state_calls == []

    @pytest.mark.asyncio
    async def test_manifest_fallback_for_session_state(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """When session data has no state, manifest state is used."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        manifest_state = {"restored_from": "manifest"}
        store = _SessionRestoringStore(
            session_record={"manifest": {"state": manifest_state}}
        )

        prepared = PreparedChatRuntime(
            cfg=sample_prepared.cfg,
            planner_lm=sample_prepared.planner_lm,
            delegate_lm=sample_prepared.delegate_lm,
            repository=None,
            persistence=store,
            persistence_required=False,
            identity_rows=sample_prepared.identity_rows,
        )
        ctx = ChatExecutionContext(
            prepared=prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u", email="t@t.com"),  # type: ignore[arg-type]
            session_id="session-456",
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(),
        )
        agent = ctx.prepared.planner_lm
        assert isinstance(agent, _StubAgent)

        _ = [e async for e in stream_turn(ctx, "hello")]

        assert len(agent.aimport_session_state_calls) == 1
        assert agent.aimport_session_state_calls[0] == manifest_state

    @pytest.mark.asyncio
    async def test_no_store_no_restore(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: _StubAgent,
    ) -> None:
        """When no persistence store is available, no restore occurs."""
        # session_id is None in the default context — set it to test.
        sample_context.session_id = "some-session"
        # No persistence store.
        sample_context.prepared.persistence = None
        sample_context.prepared.repository = None

        _ = [e async for e in stream_turn(sample_context, "hello")]

        assert stub_agent.aimport_session_state_calls == []


# ---------------------------------------------------------------------------
# VAL-REF-023 — Prepared runtime is shared, not rebuilt
# ---------------------------------------------------------------------------


class TestPreparedRuntimeShared:
    """VAL-REF-023: PreparedChatRuntime shared, not rebuilt across turns."""

    @pytest.mark.asyncio
    async def test_same_prepared_across_calls(
        self,
        sample_context: ChatExecutionContext,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """Multiple stream_turn calls with same prepared reuse it."""
        # First turn
        _ = [e async for e in stream_turn(sample_context, "turn1")]
        # Second turn
        _ = [e async for e in stream_turn(sample_context, "turn2")]

        # The prepared runtime is the same object.
        # (stream_turn does not rebuild LMs/repository/persistence)
        assert sample_context.prepared is sample_prepared

    @pytest.mark.asyncio
    async def test_planner_lm_not_rebuilt(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: _StubAgent,
    ) -> None:
        """The agent (planner_lm) is the same across calls."""
        agent1 = sample_context.prepared.planner_lm
        _ = [e async for e in stream_turn(sample_context, "turn1")]
        agent2 = sample_context.prepared.planner_lm

        assert agent1 is agent2
        assert agent1 is stub_agent


# ---------------------------------------------------------------------------
# VAL-REF-033 — Terminal events match RuntimeEventKind terminal semantics
# ---------------------------------------------------------------------------


class TestTerminalEvents:
    """VAL-REF-033: stream_turn terminal events have kind.is_terminal() True."""

    @pytest.mark.asyncio
    async def test_terminal_event_is_terminal(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """The last event yielded has kind.is_terminal() True."""
        events = [e async for e in stream_turn(sample_context, "hello")]

        assert len(events) >= 1
        last = events[-1]
        assert last.kind.is_terminal(), (
            f"Last event kind {last.kind} is not terminal"
        )

    @pytest.mark.asyncio
    async def test_done_is_terminal(self) -> None:
        """RuntimeEventKind.DONE.is_terminal() is True."""
        assert RuntimeEventKind.DONE.is_terminal() is True

    @pytest.mark.asyncio
    async def test_error_is_terminal(self) -> None:
        """RuntimeEventKind.ERROR.is_terminal() is True."""
        assert RuntimeEventKind.ERROR.is_terminal() is True

    @pytest.mark.asyncio
    async def test_non_terminal_kinds(self) -> None:
        """Non-terminal kinds return False for is_terminal()."""
        non_terminal = [
            RuntimeEventKind.TEXT,
            RuntimeEventKind.TOOL_CALL,
            RuntimeEventKind.STATUS,
            RuntimeEventKind.TURN_STARTED,
        ]
        for kind in non_terminal:
            assert kind.is_terminal() is False, f"{kind} should not be terminal"


# ---------------------------------------------------------------------------
# VAL-REF-007 — stream_turn respects cancel_flag (bounded ≤2 events)
# ---------------------------------------------------------------------------


class TestCancelFlagRespected:
    """VAL-REF-007: stream_turn respects cancel_flag; ≤2 events after flag
    is set, then StopAsyncIteration."""

    @pytest.mark.asyncio
    async def test_cancel_flag_set_mid_stream_stops_within_two(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """When cancel_flag is set mid-iteration, ≤2 events then stop."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        # Use a mid-stream cancelling agent that yields up to 2 events then stops.
        agent = _MidStreamCancellingAgent(max_before_cancel=3)
        prepared = PreparedChatRuntime(
            cfg=sample_prepared.cfg,
            planner_lm=agent,
            delegate_lm=sample_prepared.delegate_lm,
            repository=object(),
            persistence=None,
            persistence_required=False,
            identity_rows=None,
        )
        cancel_flag = {"cancelled": False}
        ctx = ChatExecutionContext(
            prepared=prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u", email="t@t.com"),  # type: ignore[arg-type]
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag=cancel_flag,
            controls=TurnControls(),
        )

        events: list[RuntimeEvent] = []
        async for event in stream_turn(ctx, "hello"):
            events.append(event)
            if len(events) >= 1:
                # Set cancel flag after first event.
                cancel_flag["cancelled"] = True

        # Should have ≤2 events after flag set.
        # (1 event before flag, then cancel-check triggers stop within 2 more)
        assert 1 <= len(events) <= 3

    @pytest.mark.asyncio
    async def test_cancel_flag_respected_in_cancel_check(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: _StubAgent,
    ) -> None:
        """The cancel_check passed to the agent reads ctx.cancel_flag."""
        _ = [e async for e in stream_turn(sample_context, "hello")]

        assert stub_agent.captured_kwargs is not None
        cancel_check = stub_agent.captured_kwargs["cancel_check"]

        # Initially False
        assert cancel_check() is False

        # After setting flag
        sample_context.cancel_flag["cancelled"] = True
        assert cancel_check() is True


# ---------------------------------------------------------------------------
# VAL-REF-035 — Cancellation before first event
# ---------------------------------------------------------------------------


class TestCancelBeforeFirstEvent:
    """VAL-REF-035: cancel_flag set before iteration → ≤1 terminal event
    then StopAsyncIteration."""

    @pytest.mark.asyncio
    async def test_cancel_before_first_event_yields_at_most_one_terminal(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """When cancel_flag is set before stream_turn starts, yields ≤1
        terminal event then StopAsyncIteration."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        agent = _NeverYieldsAgent()
        prepared = PreparedChatRuntime(
            cfg=sample_prepared.cfg,
            planner_lm=agent,
            delegate_lm=sample_prepared.delegate_lm,
            repository=object(),
            persistence=None,
            persistence_required=False,
            identity_rows=None,
        )
        cancel_flag = {"cancelled": True}  # Set before iteration.
        ctx = ChatExecutionContext(
            prepared=prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u", email="t@t.com"),  # type: ignore[arg-type]
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag=cancel_flag,
            controls=TurnControls(),
        )

        events: list[RuntimeEvent] = []
        async for event in stream_turn(ctx, "hello"):
            events.append(event)

        # ≤1 terminal event — the runtime yields a single DONE when cancelled.
        assert len(events) <= 1
        if events:
            assert events[0].kind.is_terminal()

    @pytest.mark.asyncio
    async def test_cancel_flag_set_before_stops_promptly(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """No content events when cancelled before iteration (only maybe terminal)."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        agent = _NeverYieldsAgent()
        prepared = PreparedChatRuntime(
            cfg=sample_prepared.cfg,
            planner_lm=agent,
            delegate_lm=sample_prepared.delegate_lm,
            repository=object(),
            persistence=None,
            persistence_required=False,
            identity_rows=None,
        )
        cancel_flag = {"cancelled": True}
        ctx = ChatExecutionContext(
            prepared=prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u", email="t@t.com"),  # type: ignore[arg-type]
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag=cancel_flag,
            controls=TurnControls(),
        )

        events = [e async for e in stream_turn(ctx, "hello")]

        # No content events (like TEXT, STATUS, TOOL_CALL).
        for event in events:
            assert event.kind.is_terminal() or event.kind == RuntimeEventKind.DONE, (
                f"Expected terminal/DONE event, got {event.kind}"
            )


# ---------------------------------------------------------------------------
# Helper: _build_stream_kwargs unit test
# ---------------------------------------------------------------------------


class TestBuildStreamKwargs:
    """Unit tests for the internal _build_stream_kwargs helper."""

    def test_includes_message_and_cancel_check(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """kwargs always includes message and cancel_check."""
        kwargs = _build_stream_kwargs(sample_context, "test-msg")
        assert kwargs["message"] == "test-msg"
        assert callable(kwargs["cancel_check"])

    def test_cancel_check_returns_false_by_default(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """cancel_check returns False when cancel_flag is not set."""
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["cancel_check"]() is False

    def test_cancel_check_returns_true_when_set(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """cancel_check returns True when cancel_flag['cancelled'] is True."""
        sample_context.cancel_flag["cancelled"] = True
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["cancel_check"]() is True

    def test_trace_field(self, sample_context: ChatExecutionContext) -> None:
        """trace is in kwargs when set."""
        sample_context.controls.trace = False
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["trace"] is False

    def test_docs_path_field(self, sample_context: ChatExecutionContext) -> None:
        """docs_path is in kwargs when set."""
        sample_context.controls.docs_path = "./docs"
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["docs_path"] == "./docs"

    def test_repo_url_field(self, sample_context: ChatExecutionContext) -> None:
        """repo_url is in kwargs when set."""
        sample_context.controls.repo_url = "https://example.com/repo"
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["repo_url"] == "https://example.com/repo"

    def test_repo_ref_field(self, sample_context: ChatExecutionContext) -> None:
        """repo_ref is in kwargs when set."""
        sample_context.controls.repo_ref = "main"
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["repo_ref"] == "main"

    def test_context_paths_field(self, sample_context: ChatExecutionContext) -> None:
        """context_paths is in kwargs when non-empty."""
        sample_context.controls.context_paths = ["src/"]
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["context_paths"] == ["src/"]

    def test_context_paths_empty_not_in_kwargs(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """Empty context_paths is not forwarded."""
        sample_context.controls.context_paths = []
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert "context_paths" not in kwargs

    def test_batch_concurrency_field(self, sample_context: ChatExecutionContext) -> None:
        """batch_concurrency is in kwargs when set."""
        sample_context.controls.batch_concurrency = 3
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["batch_concurrency"] == 3

    def test_trace_mode_field(self, sample_context: ChatExecutionContext) -> None:
        """trace_mode is in kwargs when set."""
        sample_context.controls.trace_mode = "full"
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["trace_mode"] == "full"

    def test_selected_skill_ids_field(self, sample_context: ChatExecutionContext) -> None:
        """selected_skill_ids is in kwargs when non-empty."""
        sample_context.controls.selected_skill_ids = ["skill-a"]
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert kwargs["selected_skill_ids"] == ["skill-a"]

    def test_selected_skill_ids_empty_not_in_kwargs(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """Empty selected_skill_ids is not forwarded."""
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert "selected_skill_ids" not in kwargs
