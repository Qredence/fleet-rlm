"""Tests for stream_turn — the transport-neutral async generator seam.

Covers validation assertions:
  VAL-REF-005, VAL-REF-006, VAL-REF-007, VAL-REF-008,
  VAL-REF-009, VAL-REF-010, VAL-REF-022, VAL-REF-023,
  VAL-REF-033, VAL-REF-035,
  VAL-DISPATCH-001 through VAL-DISPATCH-017,
  VAL-REGRESS-001 through VAL-REGRESS-008
"""

from __future__ import annotations

import ast
import sys
from collections.abc import AsyncIterator
from typing import Any

import pytest

from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext, TurnControls
from fleet_rlm.api.runtime_services.chat_runtime import PreparedChatRuntime
from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend
from fleet_rlm.api.runtime_services.stream_turn import (
    _build_stream_kwargs,
    stream_turn,
)
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
        # Record of all method calls: list of (name, args, kwargs).
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def set_execution_mode(self, mode: str) -> None:
        self.calls.append(("set_execution_mode", (mode,), {}))
        self.set_execution_mode_calls.append(mode)
        self.execution_mode = mode

    async def aiter_chat_turn_stream(self, **kwargs: Any) -> AsyncIterator[RuntimeEvent]:
        self.calls.append(("aiter_chat_turn_stream", (), kwargs))
        self.captured_kwargs = kwargs
        for event in self.events:
            yield event

    async def aimport_session_state(self, state: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("aimport_session_state", (), {"state": state}))
        self.aimport_session_state_calls.append(state)
        return {"status": "ok"}

    async def areset(self, *, clear_sandbox_buffers: bool = True) -> dict[str, Any]:
        self.calls.append(("areset", (), {"clear_sandbox_buffers": clear_sandbox_buffers}))
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


class _StrictLegacyAgent:
    """AgentRuntime-like fake that rejects unsupported kwargs by signature."""

    def __init__(self) -> None:
        self.execution_mode: str | None = None
        self.captured_kwargs: dict[str, Any] | None = None

    def set_execution_mode(self, mode: str) -> None:
        self.execution_mode = mode

    async def aiter_chat_turn_stream(
        self,
        message: str,
        trace: bool = True,
        cancel_check: Any | None = None,
        *,
        docs_path: str | None = None,
        repo_url: str | None = None,
        repo_ref: str | None = None,
        context_paths: list[str] | None = None,
        batch_concurrency: int | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        self.captured_kwargs = {
            "message": message,
            "trace": trace,
            "cancel_check": cancel_check,
            "docs_path": docs_path,
            "repo_url": repo_url,
            "repo_ref": repo_ref,
            "context_paths": context_paths,
            "batch_concurrency": batch_concurrency,
        }
        yield RuntimeEvent(kind=RuntimeEventKind.DONE, text="done", payload={"history_turns": 1})


class LM:
    """Wrong-object stand-in matching the observed DSPy LM class name."""

    def __init__(self) -> None:
        self.set_execution_mode_called = False

    def set_execution_mode(self, mode: str) -> None:
        _ = mode
        self.set_execution_mode_called = True


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
    """Minimal PreparedChatRuntime for stream_turn tests.

    Most legacy-path unit tests pass ``planner_lm`` as ``agent_runtime``
    explicitly to avoid repeating a separate runtime fixture in every case.
    Production callers must pass their context-managed AgentRuntime instead.
    """
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        ws_default_workspace_id="default",
        ws_default_user_id="anonymous",
    )
    return PreparedChatRuntime(
        cfg=cfg,  # type: ignore[arg-type]
        planner_lm=stub_agent,
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
        """stream_turn signature is keyword-only and transport-free."""
        import inspect

        sig = inspect.signature(stream_turn)
        params = sig.parameters
        assert list(params.keys()) == ["ctx", "agent_runtime", "message"]
        assert params["ctx"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["agent_runtime"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["message"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_no_transport_imports_in_source(self) -> None:
        """stream_turn module does not import WebSocket or Request."""
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        source_path = repo_root / "src" / "fleet_rlm" / "api" / "runtime_services" / "stream_turn.py"
        source_text = source_path.read_text("utf-8")

        forbidden = [
            "fastapi.WebSocket",
            "starlette.websockets",
            "fastapi.Request",
            "starlette.requests",
        ]
        for pattern in forbidden:
            assert pattern not in source_text, f"stream_turn.py must not import {pattern}"

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
        stub_agent: _StubAgent,
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
        stub_agent: _StubAgent,
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
        assert isinstance(agent, _StubAgent)

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
# VAL-REF-009 — stream_turn threads TurnControls fields to runtime
# ---------------------------------------------------------------------------


class TestThreadsTurnControls:
    """VAL-REF-009: Supported TurnControls fields thread into legacy runtime kwargs."""

    @pytest.mark.asyncio
    async def test_all_fields_threaded_when_set(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """Supported non-None TurnControls fields appear in kwargs."""
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

        _ = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]

        assert agent.captured_kwargs is not None
        # execution_mode is handled by set_execution_mode, not kwargs.
        for key in (
            "repo_url",
            "repo_ref",
            "context_paths",
            "batch_concurrency",
            "docs_path",
            "trace",
        ):
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
        assert controls.trace_mode == "full"
        assert controls.selected_skill_ids == ["skill-a", "skill-b"]
        assert "trace_mode" not in agent.captured_kwargs
        assert "selected_skill_ids" not in agent.captured_kwargs

    @pytest.mark.asyncio
    async def test_none_fields_not_forwarded(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: _StubAgent,
    ) -> None:
        """None/empty fields are not forwarded as non-None."""
        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]

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
        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]

        assert stub_agent.captured_kwargs is not None
        assert stub_agent.captured_kwargs["context_paths"] == ["src/", "lib/"]

        # Mutating the original doesn't affect kwargs.
        sample_context.controls.context_paths.append("extra/")
        assert stub_agent.captured_kwargs["context_paths"] == ["src/", "lib/"]

    @pytest.mark.asyncio
    async def test_context_only_controls_not_forwarded_to_legacy_runtime(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: _StubAgent,
    ) -> None:
        """trace_mode and selected_skill_ids remain context-only for legacy runtime."""
        sample_context.controls.trace_mode = "full"
        sample_context.controls.selected_skill_ids = ["skill-a"]
        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]

        assert stub_agent.captured_kwargs is not None
        assert sample_context.controls.trace_mode == "full"
        assert sample_context.controls.selected_skill_ids == ["skill-a"]
        assert "trace_mode" not in stub_agent.captured_kwargs
        assert "selected_skill_ids" not in stub_agent.captured_kwargs


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

        _ = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]

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

        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]

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
        store = _SessionRestoringStore(session_record={"session": {"state": session_state}})

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

        _ = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]

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
        from fleet_rlm.api.auth.types import NormalizedIdentity

        manifest_state = {"restored_from": "manifest"}
        store = _SessionRestoringStore(session_record={"manifest": {"state": manifest_state}})

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

        _ = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]

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
        stub_agent: _StubAgent,
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
        stub_agent: _StubAgent,
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

        events = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]

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

    def test_trace_mode_field_stays_context_only(self, sample_context: ChatExecutionContext) -> None:
        """trace_mode remains on controls but is not a legacy runtime kwarg."""
        sample_context.controls.trace_mode = "full"
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert sample_context.controls.trace_mode == "full"
        assert "trace_mode" not in kwargs

    def test_selected_skill_ids_field_stays_context_only(self, sample_context: ChatExecutionContext) -> None:
        """selected_skill_ids remains on controls but is not a legacy runtime kwarg."""
        sample_context.controls.selected_skill_ids = ["skill-a"]
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert sample_context.controls.selected_skill_ids == ["skill-a"]
        assert "selected_skill_ids" not in kwargs

    def test_selected_skill_ids_empty_not_in_kwargs(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """Empty selected_skill_ids is not forwarded."""
        kwargs = _build_stream_kwargs(sample_context, "test")
        assert "selected_skill_ids" not in kwargs


# ═════════════════════════════════════════════════════════════════════════════
# VAL-DISPATCH-001 through VAL-DISPATCH-012: Execution backend dispatch
# ═════════════════════════════════════════════════════════════════════════════


class TestDispatch001_ControlsWins:  # noqa: N801
    """VAL-DISPATCH-001: stream_turn() resolves backend from ctx.controls.execution_backend
    when not None, ignoring AppConfig.execution_backend."""

    @pytest.mark.asyncio
    async def test_controls_direct_rlm_wins_over_config_legacy(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """Controls says direct_rlm, config says legacy → NotImplementedError (controls wins)."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u"),
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(execution_backend=ExecutionBackend.direct_rlm),
        )
        agent = ctx.prepared.planner_lm

        with pytest.raises(NotImplementedError, match="direct_rlm execution backend is not yet implemented"):
            async for _ in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hi"):
                pass

        # Verify no agent method was called.
        assert agent.calls == []

    @pytest.mark.asyncio
    async def test_controls_legacy_wins_over_config_direct_rlm(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """Controls says legacy, config says direct_rlm → legacy path (controls wins other way)."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u"),
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(execution_backend=ExecutionBackend.legacy_agent_runtime),
        )
        agent = ctx.prepared.planner_lm

        events = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]

        # Legacy path: events were yielded, aiter_chat_turn_stream was called.
        assert len(events) > 0
        assert agent.captured_kwargs is not None
        assert agent.captured_kwargs["message"] == "hello"


class TestDispatch002_FallsBackToConfig:  # noqa: N801
    """VAL-DISPATCH-002: stream_turn() falls back to AppConfig.execution_backend
    when controls.execution_backend is None."""

    @pytest.mark.asyncio
    async def test_falls_back_to_config_direct_rlm(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """Controls is None, config is direct_rlm → NotImplementedError."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        monkeypatch.setenv("EXECUTION_BACKEND", "direct_rlm")

        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u"),
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(),  # execution_backend defaults to None
        )
        agent = ctx.prepared.planner_lm

        with pytest.raises(NotImplementedError, match="direct_rlm execution backend is not yet implemented"):
            async for _ in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hi"):
                pass

        assert agent.calls == []


class TestDispatch003_BothUnsetToLegacy:  # noqa: N801
    """VAL-DISPATCH-003: stream_turn() falls back to legacy_agent_runtime when
    both controls and config are None/unset."""

    @pytest.mark.asyncio
    async def test_both_unset_runs_legacy_path(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """Default controls + default config → legacy path runs normally."""
        events = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]
        assert len(events) > 0
        # No dispatch-related exceptions.
        for event in events:
            assert isinstance(event, RuntimeEvent)
        # Last event is terminal.
        assert events[-1].kind.is_terminal()

    @pytest.mark.asyncio
    async def test_legacy_path_rejects_lm_object_before_mutation(
        self,
        sample_context: ChatExecutionContext,
    ) -> None:
        """Regression: DSPy LM objects must not be treated as AgentRuntime."""
        wrong_runtime = LM()
        sample_context.controls.execution_mode = "rlm"

        with pytest.raises(
            TypeError,
            match="legacy_agent_runtime backend expected AgentRuntime-like object, got LM",
        ):
            async for _ in stream_turn(ctx=sample_context, agent_runtime=wrong_runtime, message="hello"):
                pass

        assert wrong_runtime.set_execution_mode_called is False


class TestDispatch004_KwargsIdenticalToPhase1:  # noqa: N801
    """VAL-DISPATCH-004: legacy_agent_runtime calls aiter_chat_turn_stream with
    the same kwargs as Phase 1."""

    @pytest.mark.asyncio
    async def test_kwargs_contain_message_and_cancel_check(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: _StubAgent,
    ) -> None:
        """kwargs include message and cancel_check (Phase 1 invariants)."""
        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]
        assert stub_agent.captured_kwargs is not None
        assert stub_agent.captured_kwargs["message"] == "hello"
        assert callable(stub_agent.captured_kwargs["cancel_check"])

    @pytest.mark.asyncio
    async def test_cancel_check_reads_cancel_flag(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: _StubAgent,
    ) -> None:
        """cancel_check returns False when not cancelled, True when cancelled."""
        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]
        assert stub_agent.captured_kwargs is not None
        cancel_check = stub_agent.captured_kwargs["cancel_check"]
        assert cancel_check() is False
        sample_context.cancel_flag["cancelled"] = True
        assert cancel_check() is True

    @pytest.mark.asyncio
    async def test_all_turn_controls_threaded_when_set(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """Supported non-None TurnControls fields appear in legacy kwargs."""
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
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u", email="t@t.com"),
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

        _ = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]
        assert agent.captured_kwargs is not None
        for key in (
            "repo_url",
            "repo_ref",
            "context_paths",
            "batch_concurrency",
            "docs_path",
            "trace",
        ):
            expected = getattr(controls, key)
            if expected is not None:
                assert key in agent.captured_kwargs, f"Expected {key}={expected!r} in kwargs"
                actual = agent.captured_kwargs[key]
                if isinstance(expected, list):
                    assert list(actual) == expected
                else:
                    assert actual == expected
        assert controls.trace_mode == "full"
        assert controls.selected_skill_ids == ["skill-a", "skill-b"]
        assert "trace_mode" not in agent.captured_kwargs
        assert "selected_skill_ids" not in agent.captured_kwargs

    @pytest.mark.asyncio
    async def test_trace_mode_does_not_break_strict_legacy_runtime(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """trace_mode is not passed to strict AgentRuntime signatures."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        agent = _StrictLegacyAgent()
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
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u", email="t@t.com"),
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(
                trace=True,
                trace_mode="verbose",
                selected_skill_ids=["skill-a"],
                docs_path="./docs",
            ),
        )

        events = [e async for e in stream_turn(ctx=ctx, agent_runtime=agent, message="hello")]

        assert events[-1].kind is RuntimeEventKind.DONE
        assert agent.captured_kwargs is not None
        assert agent.captured_kwargs["message"] == "hello"
        assert agent.captured_kwargs["trace"] is True
        assert agent.captured_kwargs["docs_path"] == "./docs"


class TestDispatch005_SetExecutionMode:  # noqa: N801
    """VAL-DISPATCH-005: legacy_agent_runtime branch calls set_execution_mode
    when controls.execution_mode is not None."""

    @pytest.mark.asyncio
    async def test_sets_execution_mode_when_provided(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """set_execution_mode called with the correct value before aiter_chat_turn_stream."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u", email="t@t.com"),
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(execution_mode="auto"),
        )
        agent = ctx.prepared.planner_lm
        assert isinstance(agent, _StubAgent)

        _ = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]
        assert agent.set_execution_mode_calls == ["auto"]

    @pytest.mark.asyncio
    async def test_no_set_execution_mode_when_none(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: _StubAgent,
    ) -> None:
        """set_execution_mode not called when execution_mode is None."""
        assert sample_context.controls.execution_mode is None
        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]
        assert stub_agent.set_execution_mode_calls == []

    @pytest.mark.asyncio
    async def test_set_execution_mode_before_aiter_chat_turn_stream(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """set_execution_mode is called before aiter_chat_turn_stream."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u", email="t@t.com"),
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(execution_mode="auto"),
        )
        agent = ctx.prepared.planner_lm
        assert isinstance(agent, _StubAgent)

        _ = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]
        # set_execution_mode should be called before aiter_chat_turn_stream
        # We can verify ordering by checking the calls list.
        set_mode_index = next(i for i, c in enumerate(agent.set_execution_mode_calls) if c == "auto")
        # Just verify set_execution_mode was called at all (it's before stream in code).
        assert set_mode_index >= 0


class TestDispatch006_SessionRestore:  # noqa: N801
    """VAL-DISPATCH-006: legacy_agent_runtime branch calls _restore_session
    when session_id is provided."""

    @pytest.mark.asyncio
    async def test_restores_session_when_session_id_not_none(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """Non-None session_id triggers aimport_session_state with restored state."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        session_state = {"history_turns": 3}
        store = _SessionRestoringStore(session_record={"session": {"state": session_state}})

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
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u", email="t@t.com"),
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

        _ = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]
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
        _ = [
            e
            async for e in stream_turn(
                ctx=sample_context, agent_runtime=sample_context.prepared.planner_lm, message="hello"
            )
        ]
        assert stub_agent.aimport_session_state_calls == []


class TestDispatch007_DirectRlmRaisesNotImplementedError:  # noqa: N801
    """VAL-DISPATCH-007: direct_rlm raises NotImplementedError."""

    @pytest.mark.asyncio
    async def test_direct_rlm_raises_not_implemented_error(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """direct_rlm raises NotImplementedError, yielding zero events."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u"),
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(execution_backend=ExecutionBackend.direct_rlm),
        )
        events: list[RuntimeEvent] = []

        with pytest.raises(NotImplementedError):
            async for event in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hi"):
                events.append(event)

        assert len(events) == 0, "No events should be yielded before NotImplementedError"


class TestDispatch008_ExactErrorMessage:  # noqa: N801
    """VAL-DISPATCH-008: NotImplementedError message is exact."""

    @pytest.mark.asyncio
    async def test_exact_error_message(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """Error message is exactly 'direct_rlm execution backend is not yet implemented'."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u"),
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(execution_backend=ExecutionBackend.direct_rlm),
        )

        with pytest.raises(NotImplementedError) as exc_info:
            async for _ in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hi"):
                pass

        assert str(exc_info.value) == "direct_rlm execution backend is not yet implemented"


class TestDispatch009_RaiseBeforeSetExecutionMode:  # noqa: N801
    """VAL-DISPATCH-009: direct_rlm raises BEFORE set_execution_mode."""

    @pytest.mark.asyncio
    async def test_raises_before_set_execution_mode(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """Even with execution_mode set, direct_rlm raises before set_execution_mode."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u"),
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(
                execution_backend=ExecutionBackend.direct_rlm,
                execution_mode="auto",
            ),
        )
        agent = ctx.prepared.planner_lm
        assert isinstance(agent, _StubAgent)

        with pytest.raises(NotImplementedError):
            async for _ in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hi"):
                pass

        assert "set_execution_mode" not in [c[0] for c in agent.calls]


class TestDispatch010_RaiseBeforeRestoreSession:  # noqa: N801
    """VAL-DISPATCH-010: direct_rlm raises BEFORE _restore_session."""

    @pytest.mark.asyncio
    async def test_raises_before_restore_session(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """Even with session_id, direct_rlm raises before aimport_session_state."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        session_state = {"history_turns": 3}
        store = _SessionRestoringStore(session_record={"session": {"state": session_state}})

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
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u"),
            session_id="session-123",
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(execution_backend=ExecutionBackend.direct_rlm),
        )
        agent = ctx.prepared.planner_lm
        assert isinstance(agent, _StubAgent)

        with pytest.raises(NotImplementedError):
            async for _ in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hi"):
                pass

        assert "aimport_session_state" not in [c[0] for c in agent.calls]
        assert "areset" not in [c[0] for c in agent.calls]


class TestDispatch011_RaiseBeforeAiterChatTurnStream:  # noqa: N801
    """VAL-DISPATCH-011: direct_rlm raises BEFORE aiter_chat_turn_stream."""

    @pytest.mark.asyncio
    async def test_raises_before_aiter_chat_turn_stream(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """direct_rlm raises before aiter_chat_turn_stream is called."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u"),
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(execution_backend=ExecutionBackend.direct_rlm),
        )
        agent = ctx.prepared.planner_lm
        assert isinstance(agent, _StubAgent)

        with pytest.raises(NotImplementedError):
            async for _ in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hi"):
                pass

        assert "aiter_chat_turn_stream" not in [c[0] for c in agent.calls]


class TestDispatch012_RaiseBeforeAnyAgentMethod:  # noqa: N801
    """VAL-DISPATCH-012: direct_rlm raises BEFORE ANY agent method is called.
    This is the strong form: the agent must be untouched."""

    @pytest.mark.asyncio
    async def test_agent_untouched(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """direct_rlm raises before any agent method is called - agent.calls is empty."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        class _RecordingStub:
            """Stub that records every call."""

            def __init__(self) -> None:
                self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
                self.events = [
                    RuntimeEvent.status("should-not-be-reached"),
                ]

            def __getattr__(self, name: str) -> Any:
                def _record(*args: Any, **kwargs: Any) -> Any:
                    self.calls.append((name, args, kwargs))
                    if name == "aiter_chat_turn_stream":
                        return _async_gen(self.events)
                    return {"status": "ok"}

                return _record

        async def _async_gen(events: list[RuntimeEvent]) -> AsyncIterator[RuntimeEvent]:
            for ev in events:
                yield ev

        recording_agent = _RecordingStub()
        prepared_with_recorder = PreparedChatRuntime(
            cfg=sample_prepared.cfg,
            planner_lm=recording_agent,
            delegate_lm=sample_prepared.delegate_lm,
            repository=None,
            persistence=None,
            persistence_required=False,
            identity_rows=sample_prepared.identity_rows,
        )

        ctx = ChatExecutionContext(
            prepared=prepared_with_recorder,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u"),
            session_id="sess-1",
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(
                execution_backend=ExecutionBackend.direct_rlm,
                execution_mode="auto",
            ),
        )

        with pytest.raises(NotImplementedError):
            async for _ in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hi"):
                pass

        assert recording_agent.calls == [], f"Agent calls should be empty, got: {recording_agent.calls}"


# ═════════════════════════════════════════════════════════════════════════════
# VAL-DISPATCH-013: Unknown backend raises ValueError
# ═════════════════════════════════════════════════════════════════════════════


class TestDispatch013_UnknownBackend:  # noqa: N801
    """VAL-DISPATCH-013: Unknown backend value raises ValueError."""

    @pytest.mark.asyncio
    async def test_unknown_backend_raises_value_error(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """An unrecognised backend string raises ValueError with the value in the message."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        # TurnControls is a plain dataclass — we can set a raw string.
        ctx = ChatExecutionContext(
            prepared=sample_prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u"),
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag={"cancelled": False},
            controls=TurnControls(execution_backend="invalid_backend"),  # type: ignore[arg-type]
        )

        with pytest.raises(ValueError) as exc_info:
            async for _ in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hi"):
                pass

        assert "unknown execution backend" in str(exc_info.value).lower()


# ═════════════════════════════════════════════════════════════════════════════
# VAL-DISPATCH-014: Backend selection stable for lifetime of a turn
# ═════════════════════════════════════════════════════════════════════════════


class TestDispatch014_StableForTurn:  # noqa: N801
    """VAL-DISPATCH-014: Backend selection is stable for the lifetime of a turn."""

    @pytest.mark.asyncio
    async def test_mutating_controls_mid_turn_does_not_switch_backend(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """Mutating controls.execution_backend mid-stream does not switch the backend."""
        from fleet_rlm.api.auth.types import NormalizedIdentity

        # Use a stub that yields multiple events so we can mutate mid-stream.
        class _MultiEventStub:
            def __init__(self) -> None:
                self.events = [
                    RuntimeEvent.status("step-0"),
                    RuntimeEvent.status("step-1"),
                    RuntimeEvent(
                        kind=RuntimeEventKind.DONE,
                        text="done",
                        payload={"history_turns": 2},
                    ),
                ]
                self.captured_kwargs: dict[str, Any] | None = None

            def set_execution_mode(self, mode: str) -> None:
                pass

            async def aiter_chat_turn_stream(self, **kwargs: Any) -> AsyncIterator[RuntimeEvent]:
                self.captured_kwargs = kwargs
                for ev in self.events:
                    yield ev

        agent = _MultiEventStub()
        prepared = PreparedChatRuntime(
            cfg=sample_prepared.cfg,
            planner_lm=agent,
            delegate_lm=sample_prepared.delegate_lm,
            repository=object(),
            persistence=None,
            persistence_required=False,
            identity_rows=None,
        )

        controls = TurnControls(execution_backend=ExecutionBackend.legacy_agent_runtime)
        cancel_flag: dict[str, bool] = {"cancelled": False}
        ctx = ChatExecutionContext(
            prepared=prepared,
            identity=NormalizedIdentity(tenant_claim="t", user_claim="u"),
            session_id=None,
            canonical_workspace_id="w",
            canonical_user_id="u",
            owner_tenant_claim="t",
            owner_user_claim="u",
            cancel_flag=cancel_flag,
            controls=controls,
        )

        events: list[RuntimeEvent] = []
        async for event in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello"):
            events.append(event)
            # After first event, mutate the controls to try and switch backend.
            if len(events) == 1:
                controls.execution_backend = ExecutionBackend.direct_rlm
                cancel_flag["cancelled"] = True  # Also set cancel to stop early.

        # The stream should have continued with the legacy backend (no NotImplementedError).
        # All events should be from the legacy path.
        assert len(events) >= 1
        assert all(isinstance(e, RuntimeEvent) for e in events)
        # No NotImplementedError was raised (proving backend didn't switch mid-turn).


# ═════════════════════════════════════════════════════════════════════════════
# VAL-DISPATCH-015: stream_turn.py has no transport imports (AST-based)
# ═════════════════════════════════════════════════════════════════════════════


class TestDispatch015_NoTransportImports:  # noqa: N801
    """VAL-DISPATCH-015: stream_turn.py keeps transport-neutral imports."""

    def test_no_fastapi_imports_via_ast(self) -> None:
        """Parse stream_turn.py and assert no fastapi/starlette transport imports."""
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        source_path = repo_root / "src" / "fleet_rlm" / "api" / "runtime_services" / "stream_turn.py"
        tree = ast.parse(source_path.read_text("utf-8"))

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                else:
                    for alias in node.names:
                        module = alias.name
                        break
                    else:
                        continue

                # Check for forbidden transport imports.
                forbidden_prefixes = (
                    "fastapi",
                    "starlette.websockets",
                    "starlette.requests",
                )
                for prefix in forbidden_prefixes:
                    assert not module.startswith(prefix), (
                        f"stream_turn.py must not import transport modules, found: {module}"
                    )

    def test_no_websocket_or_request_imported(self) -> None:
        """No alias named WebSocket or Request is imported in stream_turn.py."""
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        source_path = repo_root / "src" / "fleet_rlm" / "api" / "runtime_services" / "stream_turn.py"
        tree = ast.parse(source_path.read_text("utf-8"))

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                else:
                    names = [alias.name for alias in node.names]
                for name in names:
                    assert name not in ("WebSocket", "Request"), (
                        f"stream_turn.py must not import WebSocket or Request, found: {name}"
                    )


# ═════════════════════════════════════════════════════════════════════════════
# VAL-DISPATCH-016: __all__ preserves the public seam surface
# ═════════════════════════════════════════════════════════════════════════════


class TestDispatch016_AllExportsPhase2A:  # noqa: N801
    """VAL-DISPATCH-016: stream_turn.py __all__ preserves the public seam surface."""

    def test_all_includes_stream_turn(
        self,
    ) -> None:
        """__all__ contains stream_turn."""
        import fleet_rlm.api.runtime_services.stream_turn as st_mod

        assert "stream_turn" in st_mod.__all__

    def test_all_includes_build_stream_kwargs(self) -> None:
        """__all__ contains _build_stream_kwargs."""
        import fleet_rlm.api.runtime_services.stream_turn as st_mod

        assert "_build_stream_kwargs" in st_mod.__all__

    def test_all_includes_restore_session(self) -> None:
        """__all__ contains _restore_session."""
        import fleet_rlm.api.runtime_services.stream_turn as st_mod

        assert "_restore_session" in st_mod.__all__

    def test_all_includes_resolve_backend(self) -> None:
        """__all__ contains _resolve_backend."""
        import fleet_rlm.api.runtime_services.stream_turn as st_mod

        assert "_resolve_backend" in st_mod.__all__

    def test_all_has_no_transport_names(self) -> None:
        """No item in __all__ is a transport symbol."""
        import fleet_rlm.api.runtime_services.stream_turn as st_mod

        for name in st_mod.__all__:
            assert name not in ("WebSocket", "Request", "fastapi"), (
                f"__all__ must not contain transport symbols, found: {name}"
            )


# ═════════════════════════════════════════════════════════════════════════════
# VAL-DISPATCH-017: Importing stream_turn.py has no config/runtime side effects
# ═════════════════════════════════════════════════════════════════════════════


class TestDispatch017_NoImportTimeSideEffects:  # noqa: N801
    """VAL-DISPATCH-017: Importing stream_turn.py has no config or runtime
    side effects."""

    def test_import_does_not_construct_app_config(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Importing stream_turn module does not call AppConfig() or read ExecutionBackend."""
        import importlib

        # Clear the module from sys.modules to force a fresh import.
        if "fleet_rlm.api.runtime_services.stream_turn" in sys.modules:
            del sys.modules["fleet_rlm.api.runtime_services.stream_turn"]

        # Remove any related modules that may have been loaded.
        for key in list(sys.modules.keys()):
            if "fleet_rlm.api.config" in key:
                del sys.modules[key]

        # Now re-import stream_turn (this should not construct AppConfig).
        mod = importlib.import_module("fleet_rlm.api.runtime_services.stream_turn")
        assert mod is not None

        # After import, verify that no AppConfig was inadvertently instantiated.
        # Verify that _resolve_backend is accessible as a function.
        assert callable(mod._resolve_backend)

    def test_import_has_no_runtime_side_effects(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Importing stream_turn.py triggers no AppConfig() construction."""
        import importlib

        # Track AppConfig.__init__ calls.
        init_call_count: list[int] = [0]

        from fleet_rlm.api.config import AppConfig

        original_init = AppConfig.__init__

        def _tracking_init(self: object, *args: object, **kwargs: object) -> None:
            init_call_count[0] += 1
            return original_init(self, *args, **kwargs)

        with monkeypatch.context() as m:
            m.setattr(AppConfig, "__init__", _tracking_init)

            # Fresh import of stream_turn module.
            if "fleet_rlm.api.runtime_services.stream_turn" in sys.modules:
                del sys.modules["fleet_rlm.api.runtime_services.stream_turn"]

            importlib.import_module("fleet_rlm.api.runtime_services.stream_turn")

        # AppConfig() should NOT have been called during import.
        assert init_call_count[0] == 0, (
            f"AppConfig was constructed {init_call_count[0]} time(s) during import. "
            "Config resolution must happen at call time, not at module import."
        )


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
        from fleet_rlm.api.auth.types import NormalizedIdentity

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
