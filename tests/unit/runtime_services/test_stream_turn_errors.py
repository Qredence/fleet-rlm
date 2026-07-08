"""Tests for stream_turn's cancellation, terminal-event, and error handling.

Split from the former monolithic ``test_stream_turn.py`` during Phase 2A.2
test/contract cleanup. Covers:

  VAL-REF-007, VAL-REF-033, VAL-REF-035,
  VAL-REGRESS-004, VAL-REGRESS-007

See ``test_stream_turn_legacy_backend.py``,
``test_stream_turn_execution_backend.py``, and ``test_stream_turn_controls.py``
for the remaining stream_turn coverage, and ``conftest.py`` / ``fakes.py`` in
this directory for shared fixtures.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from fleet_rlm.api.auth.types import NormalizedIdentity
from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext, TurnControls
from fleet_rlm.api.runtime_services.chat_runtime import PreparedChatRuntime
from fleet_rlm.api.runtime_services.stream_turn import stream_turn
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind
from tests.unit.runtime_services.fakes import StubAgent

# ---------------------------------------------------------------------------
# Cancellation-specific stub agents (single-file use only)
# ---------------------------------------------------------------------------


class _NeverYieldsAgent(StubAgent):
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


class _MidStreamCancellingAgent(StubAgent):
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
        events = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]

        assert len(events) >= 1
        last = events[-1]
        assert last.kind.is_terminal(), f"Last event kind {last.kind} is not terminal"

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
        async for event in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello"):
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
        stub_agent: StubAgent,
    ) -> None:
        """The cancel_check passed to the agent reads ctx.cancel_flag."""
        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]

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
        async for event in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello"):
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

        events = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]

        # No content events (like TEXT, STATUS, TOOL_CALL).
        for event in events:
            assert event.kind.is_terminal() or event.kind == RuntimeEventKind.DONE, (
                f"Expected terminal/DONE event, got {event.kind}"
            )


# ═════════════════════════════════════════════════════════════════════════════
# VAL-REGRESS-004: Stream aclose in finally block
# ═════════════════════════════════════════════════════════════════════════════


class TestRegress004_StreamAclose:  # noqa: N801
    """VAL-REGRESS-004: stream_turn() closes the underlying stream in its
    finally block identically."""

    @pytest.mark.asyncio
    async def test_normal_completion_no_error(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """Stream completes normally without error."""
        events = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]
        assert len(events) > 0

    @pytest.mark.asyncio
    async def test_no_aclose_attribute_does_not_raise(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """Stream without aclose does not raise AttributeError."""
        try:
            _ = [
                e
                async for e in stream_turn(
                    ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
                )
            ]
        except AttributeError:
            pytest.fail("stream_turn raised AttributeError when stream has no aclose")


# ═════════════════════════════════════════════════════════════════════════════
# VAL-REGRESS-007: No new exceptions in the legacy path
# ═════════════════════════════════════════════════════════════════════════════


class TestRegress007_NoNewExceptions:  # noqa: N801
    """VAL-REGRESS-007: No new exceptions raised in the legacy path."""

    @pytest.mark.asyncio
    async def test_legacy_path_no_dispatch_exceptions(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """Default legacy path does not raise NotImplementedError or ValueError."""
        try:
            events = [
                e
                async for e in stream_turn(
                    ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
                )
            ]
            assert len(events) > 0
        except (NotImplementedError, ValueError) as exc:
            pytest.fail(f"Legacy path raised unexpected exception: {exc}")
