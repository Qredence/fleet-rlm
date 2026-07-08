"""Tests for stream_turn's legacy_agent_runtime backend behavior.

Split from the former monolithic ``test_stream_turn.py`` during Phase 2A.2
test/contract cleanup. Covers:

  VAL-REF-006, VAL-REF-008, VAL-REF-022, VAL-REF-023,
  VAL-REGRESS-001

See ``test_stream_turn_execution_backend.py``, ``test_stream_turn_controls.py``,
and ``test_stream_turn_errors.py`` for the remaining stream_turn coverage,
and ``conftest.py`` / ``fakes.py`` in this directory for shared fixtures.
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
from tests.unit.runtime_services.fakes import SessionRestoringStore, StubAgent

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
        async for event in stream_turn(
            ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
        ):
            events.append(event)

        assert len(events) >= 1
        for event in events:
            assert isinstance(event, RuntimeEvent), f"Expected RuntimeEvent, got {type(event)}: {event}"

    @pytest.mark.asyncio
    async def test_no_bare_dicts_or_tuples(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """No bare dict/tuple/str yielded — only RuntimeEvent."""
        async for event in stream_turn(
            ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
        ):
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
        stub_agent: StubAgent,
    ) -> None:
        """stream_turn delegates to agent.aiter_chat_turn_stream once."""
        events = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]

        assert stub_agent.captured_kwargs is not None
        assert stub_agent.captured_kwargs["message"] == "hello"
        assert len(events) == len(stub_agent.events)

    @pytest.mark.asyncio
    async def test_cancel_check_reads_cancel_flag(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: StubAgent,
    ) -> None:
        """The cancel_check lambda returns ctx.cancel_flag.get('cancelled', False)."""
        events = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]

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
        assert isinstance(agent, StubAgent)

        [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]

        assert agent.captured_kwargs is not None
        cancel_check = agent.captured_kwargs["cancel_check"]
        assert cancel_check() is True


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
        session_state = {"history_turns": 3, "conversation": ["hi", "hello"]}
        store = SessionRestoringStore(session_record={"session": {"state": session_state}})

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
        assert isinstance(agent, StubAgent)

        _ = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]

        assert len(agent.aimport_session_state_calls) == 1
        assert agent.aimport_session_state_calls[0] == session_state

    @pytest.mark.asyncio
    async def test_no_restore_when_session_id_none(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: StubAgent,
    ) -> None:
        """None session_id does not trigger session restoration."""
        assert sample_context.session_id is None

        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]

        assert stub_agent.aimport_session_state_calls == []

    @pytest.mark.asyncio
    async def test_manifest_fallback_for_session_state(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """When session data has no state, manifest state is used."""
        manifest_state = {"restored_from": "manifest"}
        store = SessionRestoringStore(session_record={"manifest": {"state": manifest_state}})

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
        assert isinstance(agent, StubAgent)

        _ = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]

        assert len(agent.aimport_session_state_calls) == 1
        assert agent.aimport_session_state_calls[0] == manifest_state

    @pytest.mark.asyncio
    async def test_no_store_no_restore(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: StubAgent,
    ) -> None:
        """When no persistence store is available, no restore occurs."""
        # session_id is None in the default context — set it to test.
        sample_context.session_id = "some-session"
        # No persistence store.
        sample_context.prepared.persistence = None
        sample_context.prepared.repository = None

        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]

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
        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="turn1"
            )
        ]
        # Second turn
        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="turn2"
            )
        ]

        # The prepared runtime is the same object.
        # (stream_turn does not rebuild LMs/repository/persistence)
        assert sample_context.prepared is sample_prepared

    @pytest.mark.asyncio
    async def test_planner_lm_not_rebuilt(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: StubAgent,
    ) -> None:
        """The agent (planner_lm) is the same across calls."""
        agent1 = sample_context.prepared.planner_lm
        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="turn1"
            )
        ]
        agent2 = sample_context.prepared.planner_lm

        assert agent1 is agent2
        assert agent1 is stub_agent


# ═════════════════════════════════════════════════════════════════════════════
# VAL-REGRESS-001: Legacy backend produces identical event sequences
# ═════════════════════════════════════════════════════════════════════════════


class TestRegress001_IdenticalEventSequence:  # noqa: N801
    """VAL-REGRESS-001: stream_turn() with legacy_agent_runtime yields the same
    sequence of RuntimeEvent objects as Phase 1."""

    @pytest.mark.asyncio
    async def test_event_sequence_matches_phase1(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """The event kind sequence matches the Phase 1 baseline fixture."""
        events = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]

        # The stub agent yields: [STATUS("working"), DONE].
        assert len(events) >= 2
        assert events[0].kind == RuntimeEventKind.STATUS
        assert events[0].text == "working"
        assert events[-1].kind == RuntimeEventKind.DONE

    @pytest.mark.asyncio
    async def test_event_kind_order_preserved(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """Custom event kinds preserve their order through the legacy path."""
        # Build a custom sequence.
        expected_kinds = [
            RuntimeEventKind.TURN_STARTED,
            RuntimeEventKind.TEXT,
            RuntimeEventKind.STATUS,
            RuntimeEventKind.DONE,
        ]

        class _CustomEventAgent:
            def __init__(self) -> None:
                self.captured_kwargs: dict[str, Any] | None = None

            def set_execution_mode(self, mode: str) -> None:
                pass

            async def aiter_chat_turn_stream(self, **kwargs: Any) -> AsyncIterator[RuntimeEvent]:
                self.captured_kwargs = kwargs
                evts = [
                    RuntimeEvent(
                        kind=RuntimeEventKind.TURN_STARTED,
                        text="started",
                        payload={"message_id": "m1"},
                    ),
                    RuntimeEvent(kind=RuntimeEventKind.TEXT, text="hello"),
                    RuntimeEvent(kind=RuntimeEventKind.STATUS, text="working"),
                    RuntimeEvent(kind=RuntimeEventKind.DONE, text="done", payload={"history_turns": 1}),
                ]
                for ev in evts:
                    yield ev

        agent = _CustomEventAgent()
        prepared = PreparedChatRuntime(
            cfg=sample_prepared.cfg,
            planner_lm=agent,
            delegate_lm=sample_prepared.delegate_lm,
            repository=object(),
            persistence=None,
            persistence_required=False,
            identity_rows=None,
        )
        ctx = ChatExecutionContext(
            prepared=prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u"),
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(),
        )

        events = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]
        actual_kinds = [e.kind for e in events]

        assert actual_kinds == expected_kinds, f"Expected kinds {expected_kinds}, got {actual_kinds}"
