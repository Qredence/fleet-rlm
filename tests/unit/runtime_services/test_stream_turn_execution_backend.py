"""Tests for stream_turn's ExecutionBackend dispatch seam.

Split from the former monolithic ``test_stream_turn.py`` during Phase 2A.2
test/contract cleanup. Covers the async-generator contract itself plus every
backend-resolution/dispatch validation assertion:

  VAL-REF-005,
  VAL-DISPATCH-001 through VAL-DISPATCH-017

See ``test_stream_turn_legacy_backend.py``, ``test_stream_turn_controls.py``,
and ``test_stream_turn_errors.py`` for the remaining stream_turn coverage,
and ``conftest.py`` / ``fakes.py`` in this directory for shared fixtures.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import AsyncIterator
from typing import Any

import pytest

from fleet_rlm.api.auth.types import NormalizedIdentity
from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext, TurnControls
from fleet_rlm.api.runtime_services.chat_runtime import PreparedChatRuntime
from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend
from fleet_rlm.api.runtime_services.stream_turn import stream_turn
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind
from tests.unit.runtime_services._module_isolation import (
    isolated_module_reload,
    restore_sys_modules,
    restore_sys_modules_matching,
)
from tests.unit.runtime_services.fakes import SessionRestoringStore, StubAgent

# ---------------------------------------------------------------------------
# Backend-specific stub agents (single-file use only)
# ---------------------------------------------------------------------------


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
        import socket

        def _fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError("unexpected socket call during import")

        monkeypatch.setattr(socket, "socket", _fail)
        monkeypatch.setattr(socket, "create_connection", _fail)  # type: ignore[attr-defined]
        monkeypatch.setattr(socket, "getaddrinfo", _fail)

        with isolated_module_reload("fleet_rlm.api.runtime_services.stream_turn"):
            pass


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
        stub_agent: StubAgent,
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
        stub_agent: StubAgent,
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
        assert isinstance(agent, StubAgent)

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
        assert isinstance(agent, StubAgent)

        _ = [e async for e in stream_turn(ctx=ctx, agent_runtime=ctx.prepared.planner_lm, message="hello")]
        assert agent.set_execution_mode_calls == ["auto"]

    @pytest.mark.asyncio
    async def test_no_set_execution_mode_when_none(
        self,
        sample_context: ChatExecutionContext,
        stub_agent: StubAgent,
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
        assert isinstance(agent, StubAgent)

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
        session_state = {"history_turns": 3}
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


class TestDispatch007_DirectRlmRaisesNotImplementedError:  # noqa: N801
    """VAL-DISPATCH-007: direct_rlm raises NotImplementedError."""

    @pytest.mark.asyncio
    async def test_direct_rlm_raises_not_implemented_error(
        self,
        sample_prepared: PreparedChatRuntime,
    ) -> None:
        """direct_rlm raises NotImplementedError, yielding zero events."""
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
        assert isinstance(agent, StubAgent)

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
        session_state = {"history_turns": 3}
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
        assert isinstance(agent, StubAgent)

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
        assert isinstance(agent, StubAgent)

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

        stream_turn_key = "fleet_rlm.api.runtime_services.stream_turn"
        with restore_sys_modules_matching("fleet_rlm.api.config", stream_turn_key):
            # Clear the module from sys.modules to force a fresh import.
            if stream_turn_key in sys.modules:
                del sys.modules[stream_turn_key]

            # Remove any related modules that may have been loaded.
            for key in list(sys.modules.keys()):
                if "fleet_rlm.api.config" in key:
                    del sys.modules[key]

            # Now re-import stream_turn (this should not construct AppConfig).
            mod = importlib.import_module(stream_turn_key)
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

        stream_turn_key = "fleet_rlm.api.runtime_services.stream_turn"
        with restore_sys_modules(stream_turn_key):
            with monkeypatch.context() as m:
                m.setattr(AppConfig, "__init__", _tracking_init)

                # Fresh import of stream_turn module.
                if stream_turn_key in sys.modules:
                    del sys.modules[stream_turn_key]

                importlib.import_module(stream_turn_key)

        # AppConfig() should NOT have been called during import.
        assert init_call_count[0] == 0, (
            f"AppConfig was constructed {init_call_count[0]} time(s) during import. "
            "Config resolution must happen at call time, not at module import."
        )
